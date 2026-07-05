# AI Mock Interviewer — Voice Screening Platform

An AI-powered, real-time voice screening mock interviewer designed to help candidates prepare for interviews. The platform dynamically parses candidate resumes, reads target Job Descriptions (JD), extracts candidate details, and conducts interactive technical and behavioral screening screens over local microphone and speaker hardware.

Built with a modular, **SOLID design pattern** architecture for ultimate codebase maintainability and clean decoupling.

---

## 📂 Codebase Architecture

The application is strictly decoupled into frontend (presentation) and backend (logic/orchestration) packages:

```
c:\Users\Dell\Desktop\demo/
├── .env                                  # Local key credentials (gitignored)
├── .env.example                          # Credentials outline template
├── .gitignore                            # Excludes credentials, venv, caches, and logs
├── requirements.txt                      # Project dependency configurations
├── README.md                             # Project setup and usage instructions
├── backend/                              # Backend services
│   ├── app/
│   │   ├── core/                         # Core models & system settings
│   │   │   ├── config.py                 # Validates environment and sets models
│   │   │   └── interfaces/               # Decoupled SOLID contracts
│   │   │       ├── document_parser.py    # Abstraction for document parsers
│   │   │       ├── prompt_builder.py     # Abstraction for prompt system
│   │   │       ├── repository.py         # Abstraction for session storage
│   │   │       ├── pipeline_builder.py   # Abstraction for Pipecat pipeline wireframes
│   │   │       └── bot_runner.py         # Abstraction for thread loop runners
│   │   ├── parsers/                      # Resume parsing strategies
│   │   │   ├── pdf_parser.py             # PDF text extractor
│   │   │   ├── txt_parser.py             # Plain text/markdown parser
│   │   │   └── factory.py                # Parser Factory (OCP compliant)
│   │   ├── prompts/                      # Prompt builders
│   │   │   └── interview_prompt.py       # Interview instructions & identity (Sheela)
│   │   ├── repositories/                 # Storage repositories
│   │   │   └── json_repository.py        # Local JSON disk serializer (DIP compliant)
│   │   ├── pipeline/                     # Pipecat pipelines
│   │   │   ├── accumulator.py            # Custom Dialogue logger processor
│   │   │   └── builder.py                # Pipeline connector (STT -> LLM -> TTS)
│   │   └── runner/                       # Async thread runner managers
│   │       └── bot_runner_impl.py        # Local bot background daemon thread runner
└── frontend/                             # User Interfaces
    ├── app.py                            # Streamlit layout controls and pages
    └── ui/
        └── styles.py                     # Custom HSL dark glassmorphism CSS theme
```

---

## 🛠️ Technology Stack

* **VAD (Voice Activity Detection)**: Silero VAD (ONNX model) for low-latency voice capture.
* **STT (Speech-to-Text)**: Deepgram API WebSocket streams.
* **LLM (Language Model)**: Groq Cloud API running `llama-3.3-70b-versatile`.
* **TTS (Text-to-Speech)**: Deepgram API WebSocket streams.
* **Audio Transport**: Local microphone/speakers connection via PyAudio (PortAudio wrapper).
* **Interface Layer**: Streamlit Server (Python).
* **Dependency Manager**: `uv` (recommended) or standard `pip`.

---

## 🚀 Setup Instructions

Follow these step-by-step instructions to get the application up and running on your local machine.

### Prerequisites
* **Python**: Version 3.10 to 3.12 is recommended.
* **Windows OS**: Python standard sound dependencies (PortAudio) are packed out-of-the-box by PyAudio.
* **API Accounts**:
  * [Groq Cloud Console API Key](https://console.groq.com/)
  * [Deepgram Console API Key](https://console.deepgram.com/)

---

### Step 1: Clone and Enter Project Directory
Open your terminal (PowerShell, Command Prompt, or VS Code terminal) and navigate to the project directory:
```powershell
cd c:\Users\Dell\Desktop\demo
```

### Step 2: Configure Environment Credentials
Create a `.env` file in the root of the project to hold your API keys (this file is ignored by Git and will remain secure on your machine):

1. Copy the example configuration file:
   ```powershell
   copy .env.example .env
   ```
2. Open `.env` and fill in your actual console API keys:
   ```env
   DEEPGRAM_API_KEY=your_actual_deepgram_api_key
   GROQ_API_KEY=your_actual_groq_api_key
   ```

### Step 3: Install Dependencies
We recommend using **`uv`** as it is extremely fast and auto-configures your environment:

```powershell
# Install all required packages into the local .venv virtual environment
uv pip install -r requirements.txt
```
*(If you do not have `uv`, you can install standard packages using `pip install -r requirements.txt`)*.

---

### Step 4: Run the Application
Start the Streamlit server on your chosen port (for example, `8502` to avoid port conflicts with other running apps):

```powershell
uv run streamlit run frontend/app.py --server.port 8502
```

Once running, the terminal will print:
```text
  You can now view your Streamlit app in your browser.
  Local URL: http://localhost:8502
```
Open **[http://localhost:8502](http://localhost:8502)** in your web browser.

---

## 🎙️ How to Conduct an Interview

1. **Wear Headphones**: **Crucial!** Because we use local microphone and speakers with a highly sensitive Voice Activity Detector (VAD), wearing headphones prevents the microphone from hearing the AI's own voice (eliminating feedback loops and self-interruptions).
2. **Context Setup**:
   * Paste the target **Job Description (JD)**.
   * Upload the candidate's **Resume** (PDF or TXT).
3. **Start Interview**: Click the **"🚀 Start Voice Interview"** button.
4. **Talk Naturally**:
   * Sheela will introduce herself, welcome you by extracting your name from the resume, and ask you to introduce yourself before asking technical questions.
   * Speak naturally into your microphone. When you pause speaking, the bot will process your words and reply back.
5. **Review Transcripts**:
   * The conversation transcript saves dynamically in real-time to the local `interviews/` folder.
   * Go to the **"📂 Past Interview Records"** tab in the web interface to select, view, and read previous dialogues in clean chat bubbles!
