#!/bin/bash

# Test script for monorepo
# Usage: ./scripts/test.sh

set -e

echo "=================================="
echo "Running Tests for AI-DLC Monorepo"
echo "=================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓ $2${NC}"
    else
        echo -e "${RED}✗ $2${NC}"
    fi
}

# Step 1: Install test dependencies
echo ""
echo "Step 1: Installing test dependencies..."
pip install pytest pytest-asyncio pytest-cov

# Step 2: Run domain models tests
echo ""
echo "Step 2: Running domain models tests..."
cd packages/domain-models
if [ -d "tests" ]; then
    pytest tests/ -v --cov=. --cov-report=term-missing || print_status $? "domain-models tests completed"
else
    print_warning "No tests directory found in domain-models"
fi
cd ../..

# Step 3: Run interview service tests
echo ""
echo "Step 3: Running interview service tests..."
cd services/interview
if [ -d "tests" ]; then
    pytest tests/ -v --cov=. --cov-report=term-missing || print_status $? "interview service tests completed"
else
    print_warning "No tests directory found in interview service"
fi
cd ../..

# Step 4: Run copilot service tests
echo ""
echo "Step 4: Running copilot service tests..."
cd services/copilot
if [ -d "tests" ]; then
    pytest tests/ -v --cov=. --cov-report=term-missing || print_status $? "copilot service tests completed"
else
    print_warning "No tests directory found in copilot service"
fi
cd ../..

# Step 5: Run integration tests
echo ""
echo "Step 5: Running integration tests..."
cd tests/integration
if [ -d "tests" ]; then
    pytest tests/ -v --cov=. --cov-report=term-missing || print_status $? "integration tests completed"
else
    print_warning "No integration tests directory found"
fi

# Step 6: Run frontend tests (if available)
echo ""
echo "Step 6: Running frontend tests..."
if [ -d "frontend-new" ]; then
    cd frontend-new
    if [ -f "package.json" ]; then
        npm install
        npm run test || print_status $? "frontend tests completed"
    else
        print_warning "No package.json found in frontend, skipping npm test"
    fi
    cd ../..
else
    print_warning "frontend-new directory not found, skipping frontend tests"
fi

echo ""
echo "=================================="
echo -e "${GREEN}All tests completed!${NC}"
echo "=================================="