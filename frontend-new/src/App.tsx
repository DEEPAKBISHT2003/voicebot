import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout } from './layouts/Layout';
import { InterviewsList } from './pages/InterviewsList';
import { NewInterview } from './pages/NewInterview';
import { InterviewSession } from './pages/InterviewSession';

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
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<InterviewsList />} />
            <Route path="/interviews/new" element={<NewInterview />} />
            <Route path="/interviews/:id" element={<InterviewSession />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
