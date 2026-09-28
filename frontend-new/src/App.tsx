import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './context/AuthContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import { AdminRoute } from './components/AdminRoute';
import { Layout } from './layouts/Layout';
import { Login } from './pages/Login';
import { InterviewsList } from './pages/InterviewsList';
import { NewInterview } from './pages/NewInterview';
import { InterviewSession } from './pages/InterviewSession';
import { NewCopilot } from './copilot/pages/NewCopilot';
import { CopilotSession } from './copilot/pages/CopilotSession';
import { UsageDashboard } from './pages/UsageDashboard';
import { UserControlPlaceholder } from './pages/AdminPlaceholders';

// Instantiate Query Client for server state caching
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            {/* Public Login Route */}
            <Route path="/login" element={<Login />} />

            {/* Protected Routes (USER and ADMIN) */}
            <Route element={<ProtectedRoute />}>
              <Route
                element={
                  <Layout>
                    <Routes>
                      <Route path="/" element={<InterviewsList />} />
                      <Route path="/interviews/new" element={<NewInterview />} />
                      <Route path="/interviews/:id" element={<InterviewSession />} />
                      <Route path="/copilots/new" element={<NewCopilot />} />
                      <Route path="/copilots/:id" element={<CopilotSession />} />

                      {/* Admin-only Routes */}
                      <Route element={<AdminRoute />}>
                        <Route path="/analytics" element={<UsageDashboard />} />
                        <Route path="/usage" element={<UsageDashboard />} />
                        <Route path="/user-control" element={<UserControlPlaceholder />} />
                      </Route>

                      <Route path="*" element={<Navigate to="/" replace />} />
                    </Routes>
                  </Layout>
                }
                path="/*"
              />
            </Route>

            {/* Fallback */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
