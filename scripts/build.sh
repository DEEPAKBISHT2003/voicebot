#!/bin/bash

# Build script for monorepo
# Usage: ./scripts/build.sh

set -e

echo "=================================="
echo "Building AI-DLC Monorepo"
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
        exit 1
    fi
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Step 1: Build domain models package
echo ""
echo "Step 1: Building domain-models package..."
cd packages/domain-models
if [ -f "package.json" ]; then
    npm install
    npm run build
    print_status $? "domain-models built successfully"
else
    print_warning "No package.json found in domain-models, skipping npm build"
fi
cd ../..

# Step 2: Build adapters package
echo ""
echo "Step 2: Building adapters package..."
cd packages/adapters
if [ -f "package.json" ]; then
    npm install
    npm run build
    print_status $? "adapters built successfully"
else
    print_warning "No package.json found in adapters, skipping npm build"
fi
cd ../..

# Step 3: Build infrastructure package
echo ""
echo "Step 3: Building infrastructure package..."
cd packages/infrastructure
if [ -f "package.json" ]; then
    npm install
    npm run build
    print_status $? "infrastructure built successfully"
else
    print_warning "No package.json found in infrastructure, skipping npm build"
fi
cd ../..

# Step 4: Build types package
echo ""
echo "Step 4: Building types package..."
cd packages/types
if [ -f "package.json" ]; then
    npm install
    npm run build
    print_status $? "types built successfully"
else
    print_warning "No package.json found in types, skipping npm build"
fi
cd ../..

# Step 5: Build interview service
echo ""
echo "Step 5: Building interview service..."
cd services/interview
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    print_status $? "interview service dependencies installed"
else
    print_warning "No requirements.txt found in interview service"
fi
cd ../..

# Step 6: Build copilot service
echo ""
echo "Step 6: Building copilot service..."
cd services/copilot
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    print_status $? "copilot service dependencies installed"
else
    print_warning "No requirements.txt found in copilot service"
fi
cd ../..

# Step 7: Build frontend
echo ""
echo "Step 7: Building frontend..."
if [ -d "frontend-new" ]; then
    cd frontend-new
    if [ -f "package.json" ]; then
        npm install
        npm run build
        print_status $? "frontend built successfully"
    else
        print_warning "No package.json found in frontend, skipping npm build"
    fi
    cd ../..
else
    print_warning "frontend-new directory not found, skipping frontend build"
fi

echo ""
echo "=================================="
echo -e "${GREEN}Build completed successfully!${NC}"
echo "=================================="