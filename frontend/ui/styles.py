import streamlit as st

def apply_custom_styles() -> None:
    """Injects premium glassmorphic dark-theme styles into the Streamlit session context."""
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap');

        /* Global styling resets */
        html, body, [class*="css"] {
            font-family: 'Outfit', sans-serif;
        }
        
        .stApp {
            background: linear-gradient(135deg, #09090e 0%, #110e21 50%, #06050b 100%);
        }

        /* Hide standard Streamlit header and footer */
        #MainMenu, footer { visibility: hidden; }
        header { background-color: transparent !important; }

        /* Custom Glassmorphism Cards */
        .glass-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            padding: 30px;
            backdrop-filter: blur(25px);
            margin-bottom: 25px;
            transition: border 0.3s ease, box-shadow 0.3s ease;
        }
        .glass-card:hover {
            border-color: rgba(167, 139, 250, 0.3);
            box-shadow: 0 10px 40px rgba(124, 58, 237, 0.15);
        }

        /* Page Titles */
        .title-gradient {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 3.2rem;
            font-weight: 700;
            background: linear-gradient(90deg, #a78bfa 0%, #818cf8 50%, #34d399 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 5px;
        }
        
        .subtitle {
            font-size: 1.1rem;
            color: rgba(255, 255, 255, 0.6);
            margin-bottom: 35px;
        }

        /* Sidebar customize */
        [data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.02);
            backdrop-filter: blur(15px);
            border-right: 1px solid rgba(255, 255, 255, 0.06);
        }

        /* Input text areas and file dropzones */
        .stTextArea textarea, .stTextInput input {
            background: rgba(255, 255, 255, 0.04) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 12px !important;
            color: #f3f4f6 !important;
            font-size: 0.95rem !important;
            transition: border-color 0.25s ease !important;
        }
        .stTextArea textarea:focus, .stTextInput input:focus {
            border-color: #a78bfa !important;
        }
        .stFileUploader {
            background: rgba(255, 255, 255, 0.02) !important;
            border: 1px dashed rgba(167, 139, 250, 0.3) !important;
            border-radius: 14px !important;
            padding: 10px !important;
        }

        /* Buttons override */
        .stButton > button {
            border-radius: 12px !important;
            font-weight: 600 !important;
            padding: 12px 24px !important;
            transition: all 0.2s ease-in-out !important;
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%) !important;
            border: none !important;
            box-shadow: 0 4px 15px rgba(124, 58, 237, 0.3) !important;
            color: white !important;
        }
        .stButton > button[kind="primary"]:hover {
            opacity: 0.9 !important;
            transform: translateY(-1px) !important;
        }

        /* Talk dialog bubbles */
        .bubble-container {
            display: flex;
            flex-direction: column;
            gap: 15px;
            margin-top: 15px;
        }
        .bubble-user {
            background: rgba(59, 130, 246, 0.15);
            border: 1px solid rgba(59, 130, 246, 0.25);
            border-radius: 18px 18px 0px 18px;
            padding: 16px 20px;
            align-self: flex-end;
            max-width: 80%;
            color: #e0e7ff;
            animation: slideInUp 0.3s ease;
        }
        .bubble-assistant {
            background: rgba(124, 58, 237, 0.15);
            border: 1px solid rgba(124, 58, 237, 0.25);
            border-radius: 18px 18px 18px 0px;
            padding: 16px 20px;
            align-self: flex-start;
            max-width: 80%;
            color: #f5f3ff;
            animation: slideInUp 0.3s ease;
        }
        .role-badge {
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            margin-bottom: 6px;
        }
        .role-user { color: #60a5fa; }
        .role-assistant { color: #a78bfa; }

        @keyframes slideInUp {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Waveform Animation */
        .wave-container {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
            height: 50px;
            margin: 20px 0;
        }
        .wave-bar {
            width: 4px;
            height: 15px;
            background: linear-gradient(180deg, #a78bfa 0%, #4f46e5 100%);
            border-radius: 2px;
            animation: pulseWave 1.2s ease-in-out infinite;
        }
        @keyframes pulseWave {
            0%, 100% { height: 15px; }
            50% { height: 45px; }
        }

        /* Streamlit Native Bordered Container Custom Override */
        div[data-testid="stVerticalBlockBorder"] {
            background: rgba(255, 255, 255, 0.03) !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            border-radius: 20px !important;
            padding: 30px !important;
            backdrop-filter: blur(25px) !important;
            margin-bottom: 25px !important;
            transition: border 0.3s ease, box-shadow 0.3s ease !important;
        }
        div[data-testid="stVerticalBlockBorder"]:hover {
            border-color: rgba(167, 139, 250, 0.3) !important;
            box-shadow: 0 10px 40px rgba(124, 58, 237, 0.15) !important;
        }
    </style>
    """, unsafe_allow_html=True)
