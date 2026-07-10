import os
import sys
import time
import datetime
import httpx
import socket
from urllib.parse import urlparse
from dotenv import load_dotenv
import streamlit as st

# Setup python path to load the backend package
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import SOLID services
from backend.app.parsers.factory import DocumentParserFactory
from frontend.ui.styles import apply_custom_styles

load_dotenv(override=True)

def get_local_ip() -> str:
    """Retrieve the primary local IP address of the laptop dynamically."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def extract_candidate_name(resume_text: str) -> str:
    """Dynamically extracts the candidate's name from the resume text (first line)."""
    if not resume_text:
        return "Unknown Candidate"
    lines = [line.strip() for line in resume_text.split("\n") if line.strip()]
    if not lines:
        return "Unknown Candidate"
    first_line = lines[0]
    # Clean up name from email, phone numbers, pipes, or commas
    name = first_line.split(',')[0].split('|')[0].split('+')[0].split(' - ')[0].strip()
    words = name.split()
    if len(words) > 4:
        name = " ".join(words[:3])
    if not name or name.isdigit():
        return "Candidate"
    return name

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
# 2. Session State Initialization
# ──────────────────────────────────────────────────────────────────────────────
if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.session_state.session_id = ""
    st.session_state.status = "Stopped"
    st.session_state.transcript = []
    st.session_state.jd_text = ""
    st.session_state.resume_text = ""
    st.session_state.custom_prompt_text = ""
    st.session_state.is_active = False
    st.session_state.selected_record_id = ""

# Check API Keys (Warning if not loaded in backend environment)
keys_loaded = bool(os.getenv("DEEPGRAM_API_KEY") and os.getenv("GROQ_API_KEY"))

# Backend URL configurations resolved dynamically
LOCAL_IP = get_local_ip()
BACKEND_URL = os.getenv("BACKEND_URL", f"http://{LOCAL_IP}:8000")

# Derive WebSocket connection port
parsed_url = urlparse(BACKEND_URL)
BACKEND_PORT = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)



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
            try:
                httpx.post(f"{BACKEND_URL}/api/interviews/{st.session_state.session_id}/stop")
            except Exception:
                pass
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
        # Configuration setup UI in native container styled with glassmorphism
        with st.container(border=True):
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
                    # Save raw bytes and original name in session state
                    st.session_state.resume_raw_bytes = resume_file.getvalue()
                    st.session_state.resume_filename = resume_file.name
                    
                    # Extract text using SOLID Factory & Parser
                    try:
                        parser = DocumentParserFactory.get_parser(resume_file.name)
                        extracted_resume = parser.parse(st.session_state.resume_raw_bytes, resume_file.name)
                        st.success(f"Successfully extracted {len(extracted_resume)} characters from Resume!")
                        st.session_state.resume_text = extracted_resume
                    except Exception as e:
                        st.error(f"Error parsing resume: {e}")
                elif st.session_state.resume_text:
                    st.info("Resume text loaded from active session.")
                    
                st.markdown("<br>", unsafe_allow_html=True)
                custom_prompt_input = st.text_area(
                    "Custom System Instructions / Guidelines (Optional)",
                    placeholder="Examples:\n- 'Conduct the entire mock interview in Spanish'\n- 'Focus heavily on Python and SQL questions'\n- 'Be extremely formal and strict'",
                    height=130,
                    value=st.session_state.custom_prompt_text
                )
        
        # Start Trigger
        ready_to_start = bool(jd_input.strip() and st.session_state.resume_text.strip() and keys_loaded)
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚀 Start Voice Interview", type="primary", disabled=not ready_to_start, use_container_width=True):
            st.session_state.jd_text = jd_input
            st.session_state.custom_prompt_text = custom_prompt_input
            st.session_state.transcript = []
            st.session_state.status = "Initializing..."
            st.session_state.timestamp = datetime.datetime.now().isoformat()
            
            # Request backend server to generate UUID, create folder, and start the local audio loop
            try:
                import base64
                resume_base64 = ""
                if "resume_raw_bytes" in st.session_state and st.session_state.resume_raw_bytes:
                    resume_base64 = base64.b64encode(st.session_state.resume_raw_bytes).decode("utf-8")
                
                response = httpx.post(
                    f"{BACKEND_URL}/api/interviews/start",
                    json={
                        "jd": st.session_state.jd_text,
                        "resume": st.session_state.resume_text,
                        "custom_prompt": st.session_state.custom_prompt_text,
                        "resume_filename": st.session_state.get("resume_filename", "resume.txt"),
                        "resume_base64": resume_base64
                    },
                    timeout=15.0
                )
                if response.status_code == 200:
                    res_data = response.json()
                    st.session_state.session_id = res_data["session_id"]
                    st.session_state.status = res_data["status"]
                    st.session_state.is_active = True
                    st.rerun()
                else:
                    st.error(f"Backend failed to start mock interview: {response.text}")
            except Exception as e:
                st.error(f"Failed to connect to backend server: {e}. Please ensure the backend is running.")
            
    else:
        # Active interview session loop UI in native container styled with glassmorphism
        with st.container(border=True):
            st.markdown(f"### 🎙️ Session: {st.session_state.session_id[:8]}... — Mock Interview Screen is Live")
            
            html_audio_streamer = f"""
            <div style="display:none;">WebSocket Audio Streamer</div>
            <script>
                const session_id = "{st.session_state.session_id}";
                
                if (window.activeAudioSession === session_id) {{
                    console.log("Audio session already running:", session_id);
                }} else {{
                    // Clean up any existing active session first
                    if (window.stopAudio) {{
                        try {{
                            window.stopAudio();
                        }} catch(e) {{
                            console.error("Error stopping previous session:", e);
                        }}
                    }}
                    
                    window.activeAudioSession = session_id;
                    const wsHost = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") ? "127.0.0.1" : window.location.hostname;
                    const wsPort = "{BACKEND_PORT}";
                    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                    const wsUrl = wsProtocol + "//" + wsHost + ":" + wsPort + "/api/ws/interview/" + session_id;
                    
                    // Attach references to window so they persist across Streamlit reruns
                    window.audioWS = null;
                    window.audioContext = null;
                    window.micStream = null;
                    window.scriptProcessor = null;
                    window.nextStartTime = 0;
                    
                    window.playChunk = function(audioData) {{
                        if (!window.audioContext) return;
                        if (window.audioContext.state === 'suspended') {{
                            window.audioContext.resume();
                        }}
                        
                        const audioBuffer = window.audioContext.createBuffer(1, audioData.length, 16000);
                        audioBuffer.getChannelData(0).set(audioData);
                        
                        const sourceNode = window.audioContext.createBufferSource();
                        sourceNode.buffer = audioBuffer;
                        sourceNode.connect(window.audioContext.destination);
                        
                        const currentTime = window.audioContext.currentTime;
                        const jitterBuffer = 0.15; // 150ms safety buffer to absorb network delay
                        if (window.nextStartTime < currentTime + jitterBuffer) {{
                            window.nextStartTime = currentTime + jitterBuffer;
                        }}
                        
                        sourceNode.start(window.nextStartTime);
                        window.nextStartTime += audioBuffer.duration;
                    }};
                    
                    window.stopAudio = function() {{
                        if (window.scriptProcessor) {{
                            try {{ window.scriptProcessor.disconnect(); }} catch(e) {{}}
                            window.scriptProcessor = null;
                        }}
                        if (window.micStream) {{
                            try {{ window.micStream.getTracks().forEach(track => track.stop()); }} catch(e) {{}}
                            window.micStream = null;
                        }}
                        if (window.audioContext) {{
                            try {{ window.audioContext.close(); }} catch(e) {{}}
                            window.audioContext = null;
                        }}
                        if (window.audioWS) {{
                            try {{ window.audioWS.close(); }} catch(e) {{}}
                            window.audioWS = null;
                        }}
                        window.activeAudioSession = null;
                    }};
                    
                    async function startAudio() {{
                        try {{
                            window.audioContext = new (window.AudioContext || window.webkitAudioContext)({{ sampleRate: 16000 }});
                            window.micStream = await navigator.mediaDevices.getUserMedia({{ audio: {{
                                echoCancellation: true,
                                noiseSuppression: true,
                                autoGainControl: true
                            }} }});
                            
                            const source = window.audioContext.createMediaStreamSource(window.micStream);
                            window.scriptProcessor = window.audioContext.createScriptProcessor(4096, 1, 1);
                            
                            window.scriptProcessor.onaudioprocess = (e) => {{
                                const inputData = e.inputBuffer.getChannelData(0);
                                const pcmBuffer = new Int16Array(inputData.length);
                                for (let i = 0; i < inputData.length; i++) {{
                                    let val = Math.max(-1, Math.min(1, inputData[i]));
                                    pcmBuffer[i] = val < 0 ? val * 0x8000 : val * 0x7FFF;
                                }}
                                
                                if (window.audioWS && window.audioWS.readyState === WebSocket.OPEN) {{
                                    window.audioWS.send(pcmBuffer.buffer);
                                }}
                            }};
                            
                            source.connect(window.scriptProcessor);
                            window.scriptProcessor.connect(window.audioContext.destination);
                            
                            window.audioWS = new WebSocket(wsUrl);
                            window.audioWS.binaryType = "blob";
                            
                            window.audioWS.onmessage = async (event) => {{
                                if (event.data instanceof Blob) {{
                                    const arrayBuffer = await event.data.arrayBuffer();
                                    const int16Array = new Int16Array(arrayBuffer);
                                    const float32Array = new Float32Array(int16Array.length);
                                    for (let i = 0; i < int16Array.length; i++) {{
                                        float32Array[i] = int16Array[i] / (int16Array[i] < 0 ? 0x8000 : 0x7FFF);
                                    }}
                                    window.playChunk(float32Array);
                                }}
                            }};
                            
                            window.audioWS.onclose = () => {{
                                window.stopAudio();
                            }};
                            
                        }} catch (err) {{
                            console.error("Audio init error:", err);
                        }}
                    }}
                    
                    startAudio();
                    
                    window.onbeforeunload = () => {{
                        window.stopAudio();
                    }};
                }}
            </script>
            """
            st.html(html_audio_streamer, unsafe_allow_javascript=True)
            
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
                try:
                    httpx.post(f"{BACKEND_URL}/api/interviews/{st.session_state.session_id}/stop")
                except Exception:
                    pass
                st.session_state.is_active = False
                st.session_state.status = "Interview Completed and Saved."
                st.rerun()
            
        st.subheader("Dialogue Script (Saves Automatically)")
        transcript_container = st.empty()
        
        # Active polling loop to draw transcripts dynamically while thread/runner is active on backend
        while st.session_state.is_active:
            try:
                response = httpx.get(f"{BACKEND_URL}/api/interviews/{st.session_state.session_id}/status")
                if response.status_code == 200:
                    res_data = response.json()
                    st.session_state.status = res_data["status"]
                    st.session_state.transcript = res_data["transcript"]
                    st.session_state.is_active = res_data["is_active"]
                else:
                    st.session_state.is_active = False
            except Exception as e:
                st.session_state.status = f"Backend polling error: {e}"
                st.session_state.is_active = False
            
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
            
            if not st.session_state.is_active:
                break
                
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
    with st.container(border=True):
        st.subheader("Saved Interview Transcripts")
        
        # Load all records from backend
        detailed_records = []
        try:
            response = httpx.get(f"{BACKEND_URL}/api/interviews")
            if response.status_code == 200:
                detailed_records = response.json()
            else:
                st.error("Failed to load interview history from backend API.")
        except Exception as e:
            st.error(f"Cannot connect to backend server: {e}. Please ensure it is running.")
            
        if len(detailed_records) == 0:
            st.info("No saved interviews found yet. Complete a mock interview to save records.")
        else:
            # Date and text filters
            st.markdown("#### 🔍 Filter past interview sessions")
            col_search, col_start, col_end = st.columns([2, 1, 1])
            with col_search:
                search_query = st.text_input("Search by Candidate Name or Session UUID", placeholder="Type search text...")
            with col_start:
                start_date = st.date_input("Start Date", value=datetime.date.today() - datetime.timedelta(days=90))
            with col_end:
                end_date = st.date_input("End Date", value=datetime.date.today())
                
            # Filter records list
            filtered_records = []
            for r in detailed_records:
                name = extract_candidate_name(r.get("resume", ""))
                sid = r["session_id"]
                ts = r.get("timestamp", "")
                
                # Date filtering
                if ts:
                    try:
                        dt = datetime.datetime.fromisoformat(ts).date()
                        if start_date and dt < start_date:
                            continue
                        if end_date and dt > end_date:
                            continue
                    except Exception:
                        pass
                
                # Text filtering
                if search_query:
                    q = search_query.lower()
                    if q not in name.lower() and q not in sid.lower():
                        continue
                        
                filtered_records.append((name, sid, ts, r))
                
            if len(filtered_records) == 0:
                st.info("No matching interview sessions found with the current filters.")
            else:
                st.markdown("<br>", unsafe_allow_html=True)
                # Table Header
                col_h_name, col_h_uuid, col_h_date, col_h_action = st.columns([3, 4, 3, 2])
                col_h_name.markdown("**Candidate Name**")
                col_h_uuid.markdown("**Session UUID**")
                col_h_date.markdown("**Completed Timestamp**")
                col_h_action.markdown("**Action**")
                st.markdown("<hr style='margin: 4px 0 12px 0;'>", unsafe_allow_html=True)
                
                # Table Rows
                for name, sid, ts, r in filtered_records:
                    col_name, col_uuid, col_date, col_action = st.columns([3, 4, 3, 2])
                    col_name.write(name)
                    col_uuid.code(sid[:18] + "...")
                    
                    ts_str = "Unknown"
                    if ts:
                        try:
                            dt = datetime.datetime.fromisoformat(ts)
                            ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            ts_str = ts
                    col_date.write(ts_str)
                    
                    if col_action.button("📂 View Record", key=f"btn_{sid}", use_container_width=True):
                        st.session_state.selected_record_id = sid
                        
            # Display detailed panels if a record is selected
            if st.session_state.selected_record_id:
                record_data = next((r for r in detailed_records if r["session_id"] == st.session_state.selected_record_id), None)
                if record_data:
                    st.markdown("---")
                    cand_name = extract_candidate_name(record_data.get("resume", ""))
                    st.markdown(f"### 📂 Displaying Transcript for Candidate: **{cand_name}**")
                    st.markdown(f"**Session Identifier:** `{record_data['session_id']}`")
                    st.markdown(f"**Completed Timestamp:** `{record_data.get('timestamp', 'Unknown')}`")
                    
                    exp_jd, exp_resume, exp_custom, exp_script, exp_recording = st.tabs([
                        "📝 Job Description Context", 
                        "📄 Candidate Resume Context", 
                        "⚙️ Custom Guidelines",
                        "💬 Interview Transcript",
                        "🔊 Play Voice Recording"
                    ])
                    
                    with exp_jd:
                        st.text_area("Job Description Details", value=record_data.get("jd", ""), height=250, disabled=True)
                        
                    with exp_resume:
                        session_id = record_data['session_id']
                        pdf_path = os.path.join("interviews", session_id, "resume.pdf")
                        
                        if os.path.exists(pdf_path):
                            st.markdown(f"""
                            <iframe id="resume-viewer-iframe" style="width:100%; height:700px; border:none; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.15);"></iframe>
                            <script>
                                (function() {{
                                    const host = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") ? "127.0.0.1" : window.location.hostname;
                                    const port = "{BACKEND_PORT}";
                                    const protocol = window.location.protocol;
                                    const url = protocol + "//" + host + ":" + port + "/api/interviews/{session_id}/resume";
                                    document.getElementById("resume-viewer-iframe").src = url;
                                }})();
                            </script>
                            """, unsafe_allow_html=True)
                        else:
                            st.text_area("Candidate Resume Context (Extracted Text Fallback)", value=record_data.get("resume", ""), height=250, disabled=True)
                            
                    with exp_custom:
                        st.text_area("Custom System Instructions", value=record_data.get("custom_prompt", "None"), height=250, disabled=True)
                        
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
                            
                    with exp_recording:
                        st.markdown("### Play recorded voice conversation")
                        recording_path = os.path.join("interviews", record_data['session_id'], "recording.wav")
                        if os.path.exists(recording_path):
                            st.audio(recording_path, format="audio/wav")
                        else:
                            st.info("No audio recording found for this session.")
