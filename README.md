# AI Mock Interviewer - Monorepo

AI-powered real-time voice screening platform for technical interviews.

## Features

- **Real-time Voice Interview**: AI-powered voice interviews with Deepgram transcription
- **Copilot Assistance**: AI copilot provides real-time suggestions to interviewers
- **Intelligence Reports**: Generate detailed reports with AI analysis
- **Mock Interview Mode**: Simulate interview sessions for practice
- **Teams Integration**: Join Teams meetings for live interviews

## Architecture

This is a **microservices monorepo** with two independent services:

### Unified Backend Service (Port 8000)
- AI Mock Interviewer & Copilot Unified backend
- Voice pipeline coordination & transcript analysis
- Real-time WebSockets for Interview & Copilot

## Quick Start

```bash
# Install dependencies
cd frontend-new && npm install

# Start services with Docker
docker-compose up --build
```

## Structure

```
demo/
├── services/          # Unified backend services
│   ├── main.py        # Single FastAPI app entrypoint (:8000)
│   ├── interview/     # Interview API & pipeline
│   ├── copilot/       # Copilot API & pipeline
│   └── browser/       # Playwright meeting bot (:8002)
├── frontend-new/      # React frontend (:3000)
```

## Services

| Service | Port | Database | Description |
|---------|------|----------|-------------|
| Unified Backend | 8000 | PostgreSQL:5432 | AI Interviewer & Copilot API |
| Browser Service | 8002 | - | Playwright Meeting Bot |
| Frontend | 3000 | - | React Application |

## Development

### Prerequisites
- Node.js 20+
- Python 3.12+
- Docker & Docker Compose
- bun (for AI-DLC)

### Setup
1. Clone the repository
2. Install dependencies: `cd applications/frontend && npm install`
3. Configure environment variables (see `.env.example`)
4. Start services: `docker-compose up --build`

### Running Tests
```bash
# Run all tests
bash scripts/test.sh

# Run with coverage
pytest services/interview/tests/ -v --cov=.
```

### Building
```bash
# Build all services
bash scripts/build.sh

# Build specific service
cd services/interview
docker build -t interview-service:latest .
```

## API Documentation

- [Interview API](docs/api/interview-endpoints.md)
- [Copilot API](docs/api/copilot-endpoints.md)
- [Architecture](docs/architecture/monorepo-structure.md)
- [Getting Started](docs/developer/getting-started.md)

## Contributing

1. Create a feature branch
2. Add tests for new functionality
3. Run lint and tests
4. Submit a pull request

## License

MIT