# AI Mock Interviewer — Decoupled Voice Screening Platform

An AI-powered, real-time voice screening mock interviewer designed to help candidates prepare for interviews. The platform dynamically parses candidate resumes, reads target Job Descriptions (JD), extracts candidate details, and conducts interactive technical and behavioral screening screens over local microphone and speaker hardware.

Built with a modular, **SOLID design pattern** architecture, it features a fully decoupled FastAPI backend, a React + Vite + TypeScript frontend with client-side playout buffering, and a PostgreSQL database layer with Tortoise ORM.

---

## 📂 Codebase Architecture

```
demo/
├── docker-compose.yml                    # Docker orchestration config
├── .dockerignore                         # Excludes heavy/secret files from docker context
├── .gitignore                            # Excludes venv, sqlite, and credential files from Git
├── .env                                  # Local environment variables (gitignored)
├── .env.example                          # Credentials outline template
├── requirements.txt                      # Project dependency configurations
├── README.md                             # Project setup and usage instructions
├── backend/                              # Backend FastAPI service
│   ├── Dockerfile                        # Backend container recipe
│   └── app/
│       ├── main.py                       # FastAPI entrypoint & Tortoise ORM startup
│       ├── core/                         # Configuration and SOLID interfaces
│       ├── models/                       # Database schemas (interview_sessions table)
│       ├── parsers/                      # Resume parsing strategies (factory pattern)
│       ├── prompts/                      # LLM prompt templates
│       ├── repositories/                 # PostgreSQL and JSON disk repository patterns
│       └── pipeline/                     # Pipecat real-time audio orchestration pipeline
└── frontend-new/                         # React SPA Frontend UI
    ├── Dockerfile                        # Frontend Nginx container recipe
    ├── nginx.conf                        # Nginx server & routing configuration
    ├── package.json                      # Node packages configuration
    └── src/                              # React application source code
```

---

## 🛠️ Technology Stack

* **VAD (Voice Activity Detection)**: Silero VAD (ONNX model) for low-latency voice capture.
* **STT (Speech-to-Text)**: Deepgram API WebSocket streams.
* **LLM (Language Model)**: Groq Cloud API running `llama-3.3-70b-versatile`.
* **TTS (Text-to-Speech)**: Deepgram API WebSocket streams.
* **Database**: PostgreSQL (v15+) with Tortoise ORM.
* **Audio Playout Jitter Buffer**: Client-side (JS) 150ms buffer in the browser to ensure staccato-free playback.
* **Web UI**: React, Vite, TypeScript, Tailwind CSS, TanStack Query, React Hook Form.

---

## 🚀 Setup & Run Instructions

Choose one of the two deployment methods below to start the application.

### Prerequisites (For Both Paths)
* **API Accounts**:
  * [Groq Cloud Console API Key](https://console.groq.com/)
  * [Deepgram Console API Key](https://console.deepgram.com/)
* **Create Environment File**:
  Copy the template file to `.env` in the root folder:
  ```powershell
  copy .env.example .env
  ```
  Fill in your actual API keys in `.env`:
  ```env
  DEEPGRAM_API_KEY=your_actual_deepgram_api_key
  GROQ_API_KEY=your_actual_groq_api_key
  ```

---

### Path A: Docker Compose Deployment (Recommended & Easiest)

This compiles and runs the database, backend, and frontend inside isolated Docker containers with shared persistent volumes.

#### Step 1: Start Docker Desktop
Ensure that **Docker Desktop** is open and running on your host machine.

#### Step 2: Spin Up Containers
Open a terminal in the root of the project and execute:
```bash
docker compose up -d --build
```
This builds both service containers, pulls the PostgreSQL database image, mounts persistence folders, and starts the system in the background.

#### Step 3: Access the Apps
* **Frontend Web Dashboard:** Open **[http://localhost:8502](http://localhost:8502)** on your browser.
* **Backend API Docs (Swagger):** View **[http://localhost:8000/docs](http://localhost:8000/docs)**.

#### Step 4: Stop Containers
To stop the services, run:
```bash
docker compose down
```

---

### Path B: Local Native Development Setup

This setup runs services directly on your host machine.

#### Step 1: Set Up PostgreSQL
1. Ensure you have a local PostgreSQL server running.
2. Create a database named `interview` on your PostgreSQL server.
3. Configure the database credentials in your `.env` file. For example:
   ```env
   DATABASE_URL=postgres://postgres:1234@localhost:5432/interview
   ```

#### Step 2: Install Python Dependencies
We recommend using `uv` for package management:
```bash
# Initialize venv and install requirements
uv pip install -r requirements.txt
```
*(Alternatively: `pip install -r requirements.txt`)*

#### Step 3: Start the Backend Service
Run the FastAPI backend server:
```bash
uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Step 4: Start the Frontend App
In a separate terminal window, start the React development server:
```bash
cd frontend-new
npm run dev
```
Now, open your browser and navigate to **[http://localhost:3000](http://localhost:3000)**.

---

## 🎙️ How to Conduct an Interview

1. **Wear Headphones**: **Crucial!** Wearing headphones prevents your microphone from picking up the interviewer's own voice (preventing echo and audio interruption feedback loops).
2. **Setup Context**:
   * Paste the target **Job Description (JD)**.
   * Upload the candidate's **Resume** (PDF or TXT).
3. **Start Interview**: Click the **"🚀 Start Voice Interview"** button. Speak into your microphone naturally.
4. **Inspect Past Records (Tab 2)**:
   * View previous transcripts formatted as chat bubbles.
   * **Original PDF Resume View:** Displays the original uploaded PDF resume in an embedded viewer instead of raw text.
   * **Search & Filters:** Search previous candidates dynamically by Name or Session UUID, and filter records by completion date ranges.
