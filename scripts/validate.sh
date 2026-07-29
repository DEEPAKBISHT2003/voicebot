#!/bin/bash

# Validation script for Phase 4: CI/CD and Testing
# Usage: ./scripts/validate.sh

set -e

echo "=================================="
echo "Validating Phase 4: CI/CD and Testing"
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

# Validation checklist
VALIDATION_PASSED=true

echo "Checking CI/CD Pipeline..."
if [ -f ".github/workflows/ci.yml" ]; then
    print_status 0 "CI/CD pipeline file exists"
    # Check for required jobs
    if grep -q "name: Lint" .github/workflows/ci.yml; then
        print_status 0 "Lint job found"
    else
        print_status 1 "Lint job not found"
        VALIDATION_PASSED=false
    fi
    
    if grep -q "name: Test Packages" .github/workflows/ci.yml; then
        print_status 0 "Test Packages job found"
    else
        print_status 1 "Test Packages job not found"
        VALIDATION_PASSED=false
    fi
    
    if grep -q "name: Build Packages" .github/workflows/ci.yml; then
        print_status 0 "Build Packages job found"
    else
        print_status 1 "Build Packages job not found"
        VALIDATION_PASSED=false
    fi
    
    if grep -q "name: Deploy" .github/workflows/ci.yml; then
        print_status 0 "Deploy job found"
    else
        print_status 1 "Deploy job not found"
        VALIDATION_PASSED=false
    fi
else
    print_status 1 "CI/CD pipeline file not found"
    VALIDATION_PASSED=false
fi

echo ""
echo "Checking Test Suites..."
if [ -f "packages/domain-models/tests/test_interview_models.py" ]; then
    print_status 0 "Domain models test file exists"
else
    print_status 1 "Domain models test file not found"
    VALIDATION_PASSED=false
fi

if [ -f "services/interview/tests/test_interview_api.py" ]; then
    print_status 0 "Interview service test file exists"
else
    print_status 1 "Interview service test file not found"
    VALIDATION_PASSED=false
fi

if [ -f "services/copilot/tests/test_copilot_api.py" ]; then
    print_status 0 "Copilot service test file exists"
else
    print_status 1 "Copilot service test file not found"
    VALIDATION_PASSED=false
fi

if [ -f "tests/integration/test_interview_copilot.py" ]; then
    print_status 0 "Integration test file exists"
else
    print_status 1 "Integration test file not found"
    VALIDATION_PASSED=false
fi

echo ""
echo "Checking Build and Test Scripts..."
if [ -f "scripts/build.sh" ]; then
    print_status 0 "Build script exists"
else
    print_status 1 "Build script not found"
    VALIDATION_PASSED=false
fi

if [ -f "scripts/test.sh" ]; then
    print_status 0 "Test script exists"
else
    print_status 1 "Test script not found"
    VALIDATION_PASSED=false
fi

echo ""
echo "Checking Docker Configurations..."
if [ -f "services/interview/Dockerfile" ]; then
    print_status 0 "Interview service Dockerfile exists"
else
    print_status 1 "Interview service Dockerfile not found"
    VALIDATION_PASSED=false
fi

if [ -f "services/copilot/Dockerfile" ]; then
    print_status 0 "Copilot service Dockerfile exists"
else
    print_status 1 "Copilot service Dockerfile not found"
    VALIDATION_PASSED=false
fi

echo ""
echo "Checking Configuration Files..."
if [ -f "pytest.ini" ]; then
    print_status 0 "pytest configuration file exists"
else
    print_status 1 "pytest configuration file not found"
    VALIDATION_PASSED=false
fi

echo ""
echo "=================================="
if [ "$VALIDATION_PASSED" = true ]; then
    echo -e "${GREEN}Phase 4 Validation PASSED!${NC}"
    echo "All required CI/CD and testing infrastructure is in place."
else
    echo -e "${RED}Phase 4 Validation FAILED${NC}"
    echo "Some required components are missing. Please review the checklist above."
fi
echo "=================================="

if [ "$VALIDATION_PASSED" = true ]; then
    exit 0
else
    exit 1
fi