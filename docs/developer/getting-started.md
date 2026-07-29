# Getting Started

## Prerequisites

- **Node.js** 20+
- **Python** 3.12+
- **Docker** & **Docker Compose**
- **bun** (for AI-DLC workflow)

## Setup

### 1. Clone Repository

```bash
git clone <repo-url>
cd demo
```

### 2. Install Dependencies

#### Frontend

```bash
cd applications/frontend
npm install
```

#### Backend Services

```bash
# Interview Service
cd ../..
cd services/interview
pip install -r requirements.txt

# Copilot Service
cd ../copilot
pip install -r requirements.txt
```

### 3. Configure Environment

Create `.env` files in each service directory:

```bash
# services/interview/.env
DATABASE_URL=postgres://postgres:1234@localhost:5432/interview
DEEPGRAM_API_KEY=your_deepgram_key
DEEPSEEK_API_KEY=your_deepseek_key
GROQ_API_KEY=your_groq_key
CORS_ALLOWED_ORIGINS=*
```

```bash
# services/copilot/.env
DATABASE_URL=postgres://postgres:1234@localhost:5433/copilot
CORS_ALLOWED_ORIGINS=*
```

### 4. Run Development

#### Option A: Using Docker Compose (Recommended)

```bash
# Start all services
docker-compose up --build

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

#### Option B: Run Services Individually

```bash
# Start Interview Service
cd services/interview
uvicorn src.main:app --reload --port 8000

# Start Copilot Service
cd services/copilot
uvicorn src.main:app --reload --port 8001

# Start Frontend
cd applications/frontend
npm run dev
```

## Code Style

### Python
- Follow PEP 8
- Use `ruff` for linting
- Use `pytest` for testing

### TypeScript
- Use ESLint + Prettier
- Follow React best practices

### Commit Messages
- Use Conventional Commits
- Format: `type(scope): message`

## Testing

### Run All Tests

```bash
bash scripts/test.sh
```

### Run Specific Tests

```bash
# Interview Service Tests
pytest services/interview/tests/ -v

# Copilot Service Tests
pytest services/copilot/tests/ -v

# Integration Tests
pytest tests/integration/ -v
```

## Building for Production

```bash
# Build all services
bash scripts/build.sh

# Build specific service
cd services/interview
docker build -t interview-service:latest .
```

## AI-DLC Workflow

This project uses AI-DLC for structured development:

```bash
# Validate AI-DLC setup
/aidlc --doctor

# Start new feature workflow
/aidlc --scope feature

# View status
/aidlc --status
```

## Troubleshooting

### Port Already in Use
```bash
# Kill process on port 8000
lsof -i :8000
```

### Database Connection Errors
```bash
# Check PostgreSQL is running
docker-compose ps

# Restart database
docker-compose restart db-interview
```

### CORS Errors
```bash
# Update CORS_ALLOWED_ORIGINS in .env
CORS_ALLOWED_ORIGINS=http://localhost:3000
```