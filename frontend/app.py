import os
import sys
import time
import uuid
import datetime
from dotenv import load_dotenv
import streamlit as st

# Setup python path to load the backend package
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import SOLID services
from backend.app.runner.bot_runner_impl import LocalBotRunner
from backend.app.parsers.factory import DocumentParserFactory
from backend.app.repositories.json_repository import JSONFileInterviewRepository
from frontend.ui.styles import apply_custom_styles

load_dotenv(override=True)

# ──────────────────────────────────────────────────────────────────────────────
# 1. Page Config & CSS (Premium Design System)
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Mock Interviewer",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply HSL glassmorphism styles
apply_custom_styles()

# ──────────────────────────────────────────────────────────────────────────────
# 2. Session State Initialization (DIP)
# ──────────────────────────────────────────────────────────────────────────────
if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.session_state.session_id = ""
    st.session_state.status = "Stopped"
    st.session_state.transcript = []
    st.session_state.jd_text = ""
    st.session_state.resume_text = ""
    st.session_state.is_active = False
    
    # Extract API keys from backend config settings
    deepgram_key = os.getenv("DEEPGRAM_API_KEY", "")
    groq_key = os.getenv("GROQ_API_KEY", "")
    
    # Initialize SOLID components
    st.session_state.repo = JSONFileInterviewRepository()
    st.session_state.bot_runner = LocalBotRunner(deepgram_key, groq_key)

# Check API Keys
keys_loaded = bool(os.getenv("DEEPGRAM_API_KEY") and os.getenv("GROQ_API_KEY"))

# ──────────────────────────────────────────────────────────────────────────────
# 3. Sidebar Panel
# ──────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🎙️ Session Center")
    st.markdown("---")
    
    if st.session_state.is_active:
        st.markdown(f"**Session ID:**\n`{st.session_state.session_id}`")
        st.markdown(f"**Status:** :green[Active]")
        st.markdown("---")
        if st.button("🔴 Stop Interview", key="stop_sidebar_btn", use_container_width=True):
            st.session_state.bot_runner.stop()
            st.session_state.is_active = False
            st.session_state.status = "Interview Stopped."
            st.rerun()
    else:
        st.markdown("**Status:** :red[Offline]")
        st.markdown("---")
        st.info("Input a Job Description and Candidate Resume to begin the mock interview.")
        
    st.markdown("---")
    st.markdown("### ⚙️ System Config")
    if keys_loaded:
        st.success("API Keys Loaded ✅")
    else:
        st.error("Missing API Keys ⚠️")
        st.warning("Ensure DEEPGRAM_API_KEY and GROQ_API_KEY are configured in your `.env` file.")

# ──────────────────────────────────────────────────────────────────────────────
# 4. Main Panel - Content Layout
# ──────────────────────────────────────────────────────────────────────────────
st.markdown('<div class="title-gradient">AI Mock Interviewer</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">A voice-only preparation chatbot based on Job Descriptions and Candidate Resumes</div>', unsafe_allow_html=True)

# Tabs
tab_interview, tab_records = st.tabs(["🎙️ Conduct Mock Interview", "📂 Past Interview Records"])

# ── Tab 1: Mock Interview screen ──
with tab_interview:
    if not st.session_state.is_active:
        # Configuration setup UI
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("Configure Interview Context")
        
        col1, col2 = st.columns([1, 1], gap="medium")
        
        with col1:
            jd_input = st.text_area(
                "Job Description (JD)",
                placeholder="Paste the target job description details here...",
                height=300,
                value=st.session_state.jd_text
            )
            
        with col2:
            resume_file = st.file_uploader(
                "Candidate Resume (PDF or TXT)",
                type=["pdf", "txt"]
            )
            
            if resume_file:
                # Extract text using SOLID Factory & Parser
                try:
                    parser = DocumentParserFactory.get_parser(resume_file.name)
                    extracted_resume = parser.parse(resume_file.read(), resume_file.name)
                    st.success(f"Successfully extracted {len(extracted_resume)} characters from Resume!")
                    st.session_state.resume_text = extracted_resume
                except Exception as e:
                    st.error(f"Error parsing resume: {e}")
            elif st.session_state.resume_text:
                st.info("Resume text loaded from active session.")
                
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Start Trigger
        ready_to_start = bool(jd_input.strip() and st.session_state.resume_text.strip() and keys_loaded)
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚀 Start Voice Interview", type="primary", disabled=not ready_to_start, use_container_width=True):
            st.session_state.jd_text = jd_input
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.transcript = []
            st.session_state.status = "Initializing..."
            st.session_state.timestamp = datetime.datetime.now().isoformat()
            
            # Setup Callbacks
            def make_transcript_callback(sess_id):
                def callback(entry):
                    st.session_state.transcript.append(entry)
                    # Persist instantly
                    st.session_state.repo.save_session(
                        sess_id,
                        {
                            "session_id": sess_id,
                            "timestamp": st.session_state.timestamp,
                            "jd": st.session_state.jd_text,
                            "resume": st.session_state.resume_text,
                            "transcript": st.session_state.transcript
                        }
                    )
                return callback
                
            def status_callback(status_str):
                st.session_state.status = status_str

            # Start background thread bot
            st.session_state.bot_runner.start(
                jd=st.session_state.jd_text,
                resume=st.session_state.resume_text,
                session_id=st.session_state.session_id,
                status_callback=status_callback,
                transcript_callback=make_transcript_callback(st.session_state.session_id)
            )
            
            st.session_state.is_active = True
            st.rerun()
            
    else:
        # Active interview session loop UI
        st.markdown('<div class="glass-card" style="text-align: center;">', unsafe_allow_html=True)
        st.markdown(f"### 🎙️ Session: {st.session_state.session_id[:8]}... — Mock Interview Screen is Live")
        
        # Audio Waveform Animation
        st.markdown(f"""
        <div class="wave-container">
            {''.join([f'<div class="wave-bar" style="animation-delay: {i * 0.15}s;"></div>' for i in range(12)])}
        </div>
        """, unsafe_allow_html=True)
        
        status_placeholder = st.empty()
        status_placeholder.markdown(f"**Current Status:** `{st.session_state.status}`")
        
        # Stop Controls
        if st.button("🛑 Stop & Save Interview", type="primary", use_container_width=True):
            st.session_state.bot_runner.stop()
            st.session_state.is_active = False
            st.session_state.status = "Interview Completed and Saved."
            st.rerun()
            
        st.markdown("</div>", unsafe_allow_html=True)
        
        st.subheader("Dialogue Script (Saves Automatically)")
        transcript_container = st.empty()
        
        # Active polling loop to draw transcripts dynamically while thread is running
        while st.session_state.bot_runner.is_running():
            # Update status
            status_placeholder.markdown(f"**Current Status:** `{st.session_state.status}`")
            
            # Draw dialog blocks
            with transcript_container.container():
                st.markdown('<div class="bubble-container">', unsafe_allow_html=True)
                for msg in st.session_state.transcript:
                    role = msg["role"]
                    text = msg["text"]
                    role_lbl = "🗣️ Candidate" if role == "user" else "🤖 AI Interviewer"
                    role_cls = "bubble-user" if role == "user" else "bubble-assistant"
                    role_badge_cls = "role-user" if role == "user" else "role-assistant"
                    
                    st.markdown(f"""
                    <div class="{role_cls}">
                        <div class="role-badge {role_badge_cls}">{role_lbl}</div>
                        <div>{text}</div>
                    </div>
                    """, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
                
            time.sleep(0.5)
            
        # Post-loop visual confirmation
        st.markdown('<div class="bubble-container">', unsafe_allow_html=True)
        for msg in st.session_state.transcript:
            role = msg["role"]
            text = msg["text"]
            role_lbl = "🗣️ Candidate" if role == "user" else "🤖 AI Interviewer"
            role_cls = "bubble-user" if role == "user" else "bubble-assistant"
            role_badge_cls = "role-user" if role == "user" else "role-assistant"
            
            st.markdown(f"""
            <div class="{role_cls}">
                <div class="role-badge {role_badge_cls}">{role_lbl}</div>
                <div>{text}</div>
            </div>
            """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)


# ── Tab 2: Records History ──
with tab_records:
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("Saved Interview Transcripts")
    
    # Load all files in directory
    directory = "interviews"
    if not os.path.exists(directory) or len(os.listdir(directory)) == 0:
        st.info("No saved interviews found yet. Complete a mock interview to save records.")
    else:
        files = [f.replace(".json", "") for f in os.listdir(directory) if f.endswith(".json")]
        
        # Sort files by timestamp if possible
        detailed_records = []
        for fid in files:
            try:
                rec = st.session_state.repo.load_session(fid)
                detailed_records.append(rec)
            except Exception:
                pass
                
        # Sort by timestamp descending
        detailed_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        # Selection options
        record_labels = []
        record_map = {}
        for r in detailed_records:
            sid = r["session_id"]
            ts = r.get("timestamp", "Unknown date")
            try:
                dt = datetime.datetime.fromisoformat(ts)
                ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_str = ts
                
            label = f"Session {sid[:8]}... (Date: {ts_str})"
            record_labels.append(label)
            record_map[label] = r
            
        selected_label = st.selectbox("Select Interview Session", record_labels)
        
        if selected_label:
            record_data = record_map[selected_label]
            st.markdown("---")
            
            st.markdown(f"**Session Identifier:** `{record_data['session_id']}`")
            st.markdown(f"**Completed Timestamp:** `{record_data.get('timestamp', 'Unknown')}`")
            
            exp_jd, exp_resume, exp_script = st.tabs(["📝 Job Description Context", "📄 Candidate Resume Context", "💬 Interview Transcript"])
            
            with exp_jd:
                st.text_area("Job Description Details", value=record_data.get("jd", ""), height=250, disabled=True)
                
            with exp_resume:
                st.text_area("Candidate Resume Context", value=record_data.get("resume", ""), height=250, disabled=True)
                
            with exp_script:
                script_list = record_data.get("transcript", [])
                if not script_list:
                    st.warning("No dialog script logged for this session.")
                else:
                    st.markdown('<div class="bubble-container">', unsafe_allow_html=True)
                    for msg in script_list:
                        role = msg["role"]
                        text = msg["text"]
                        role_lbl = "🗣️ Candidate" if role == "user" else "🤖 AI Interviewer"
                        role_cls = "bubble-user" if role == "user" else "bubble-assistant"
                        role_badge_cls = "role-user" if role == "user" else "role-assistant"
                        
                        st.markdown(f"""
                        <div class="{role_cls}">
                            <div class="role-badge {role_badge_cls}">{role_lbl}</div>
                            <div>{text}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)
                    
    st.markdown("</div>", unsafe_allow_html=True)
