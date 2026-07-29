# Monorepo Structure

## Overview
This document describes the monorepo structure for the AI Mock Interviewer platform.

## Directory Structure
```
demo/
├── packages/          # Shared packages
│   ├── domain-models/
│   ├── adapters/
│   ├── infrastructure/
│   └── types/
├── services/          # Application services
│   ├── interview/
│   │   ├── src/
│   │   │   ├── api/
│   │   │   ├── clients/
│   │   │   ├── core/
│   │   │   ├── models/
│   │   │   ├── parsers/
│   │   │   ├── pipeline/
│   │   │   ├── prompts/
│   │   │   ├── repositories/
│   │   │   ├── services/
│   │   │   └── main.py
│   │   ├── tests/
│   │   └── Dockerfile
│   └── copilot/
│       ├── src/
│       │   ├── api/
│       │   ├── engine/
│       │   ├── models/
│       │   ├── prompts/
│       │   ├── services/
│       │   ├── websocket/
│       │   └── main.py
│       ├── tests/
│       └── Dockerfile
├── applications/      # Frontend applications
│   └── frontend/
├── tests/             # Test suites
│   └── integration/
├── infrastructure/    # Infrastructure as Code
├── scripts/           # Build and deployment scripts
│   ├── build.sh
│   ├── test.sh
│   └── validate.sh
└── docs/              # Documentation
```

## Shared Packages

### domain-models
Domain entities and value objects shared across services.
- `interview-models.ts` - Interview domain models
- `copilot-models.ts` - Copilot domain models

### adapters
External service integrations (DeepSeek, Deepgram, Groq, PostgreSQL).
- `index.ts` - Adapter exports

### infrastructure
Common utilities (logging, configuration, exceptions).
- `index.ts` - Infrastructure utilities

### types
TypeScript types for type safety.
- `index.ts` - Shared types

## Services

### Interview Service
FastAPI backend for AI interview functionality.
- **Port**: 8000
- **Database**: PostgreSQL (interview)
- **Features**:
  - Interview session management
  - Transcript collection
  - Voice pipeline coordination
  - Document parsing

### Copilot Service
FastAPI backend for AI copilot functionality.
- **Port**: 8001
- **Database**: PostgreSQL (copilot)
- **Features**:
  - Copilot session management
  - AI assistance
  - Intelligence reports
  - Transcript analysis

## Frontend Application
React application serving both Interview and Copilot.
- **Port**: 3000
- **Features**:
  - Interview dashboard
  - Copilot dashboard
  - Real-time transcription
  - Session management

## Benefits of This Structure

1. **Separation of Concerns**: Services are independent and focused
2. **Shared Code**: Common models and utilities are in shared packages
3. **Scalability**: Each service can scale independently
4. **Maintainability**: Clear boundaries make code easier to understand
5. **Testing**: Services can be tested independently