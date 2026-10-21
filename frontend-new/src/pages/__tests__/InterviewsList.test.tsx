import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { InterviewsList } from '../InterviewsList';
import * as interviewApi from '../../api/interview';
import type { InterviewSession } from '../../types';

vi.mock('../../api/interview', () => ({
  listInterviews: vi.fn(),
  getRecordingUrl: vi.fn((id: string) => `/api/interviews/${id}/recording`),
  getResumeUrl: vi.fn((id: string) => `/api/interviews/${id}/resume`),
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'admin-1', email: 'admin@voicebot.com', role: 'ADMIN' },
  }),
}));

describe('InterviewsList Component - Live Status & Actions Restriction', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
  });

  const renderWithProviders = () => {
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <InterviewsList />
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  it('displays ONGOING badge, disables View & Results buttons, and shows "Live interview in progress" message when session is_ongoing', async () => {
    const ongoingSession: InterviewSession = {
      session_id: '11111111-2222-3333-4444-555555555555',
      timestamp: '2026-10-21T10:00:00Z',
      candidate_name: 'John Doe',
      interviewer: 'Jane Interviewer',
      organizer_email: 'admin@voicebot.com',
      organizer: 'admin@voicebot.com',
      jd: 'Senior Python Engineer',
      resume: 'John Doe resume',
      custom_prompt: '',
      transcript: [],
      is_ongoing: true,
    };

    vi.mocked(interviewApi.listInterviews).mockResolvedValue([ongoingSession]);

    renderWithProviders();

    await waitFor(() => {
      expect(screen.getByText('John Doe')).toBeInTheDocument();
    });

    // 1. Check for ONGOING badge
    const badge = screen.getByText('ONGOING');
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain('bg-amber-50');

    // 2. Check for informational message
    expect(screen.getByText('Live interview in progress')).toBeInTheDocument();

    // 3. Ensure View and Results buttons are NOT rendered
    expect(screen.queryByRole('button', { name: /view/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /results/i })).toBeNull();
  });

  it('displays "No Transcript" badge and renders View & Results buttons when interview is ended without transcript', async () => {
    const noTranscriptSession: InterviewSession = {
      session_id: '22222222-3333-4444-5555-666666666666',
      timestamp: '2026-10-21T09:00:00Z',
      candidate_name: 'Alice Smith',
      interviewer: 'Bob Interviewer',
      organizer_email: 'admin@voicebot.com',
      organizer: 'admin@voicebot.com',
      jd: 'Frontend Developer',
      resume: 'Alice Smith resume',
      custom_prompt: '',
      transcript: [],
      is_ongoing: false,
    };

    vi.mocked(interviewApi.listInterviews).mockResolvedValue([noTranscriptSession]);

    renderWithProviders();

    await waitFor(() => {
      expect(screen.getByText('Alice Smith')).toBeInTheDocument();
    });

    // 1. Check for No Transcript badge
    expect(screen.getByText('No Transcript')).toBeInTheDocument();
    expect(screen.queryByText('ONGOING')).toBeNull();

    // 2. Informational live message should NOT be present
    expect(screen.queryByText('Live interview in progress')).toBeNull();

    // 3. View and Results buttons SHOULD be present
    expect(screen.getByRole('button', { name: /view/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /results/i })).toBeInTheDocument();
  });

  it('displays "Completed" badge and renders View & Results buttons when session has transcript and is not ongoing', async () => {
    const completedSession: InterviewSession = {
      session_id: '33333333-4444-5555-6666-777777777777',
      timestamp: '2026-10-21T08:00:00Z',
      candidate_name: 'Carol White',
      interviewer: 'David Interviewer',
      organizer_email: 'admin@voicebot.com',
      organizer: 'admin@voicebot.com',
      jd: 'Fullstack Dev',
      resume: 'Carol White resume',
      custom_prompt: '',
      transcript: [
        { speaker: 'Interviewer', text: 'Hello Carol' },
        { speaker: 'Carol', text: 'Hi, happy to be here' },
      ],
      is_ongoing: false,
    };

    vi.mocked(interviewApi.listInterviews).mockResolvedValue([completedSession]);

    renderWithProviders();

    await waitFor(() => {
      expect(screen.getByText('Carol White')).toBeInTheDocument();
    });

    // 1. Check for Completed badge
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.queryByText('ONGOING')).toBeNull();
    expect(screen.queryByText('No Transcript')).toBeNull();

    // 2. Buttons should be present
    expect(screen.getByRole('button', { name: /view/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /results/i })).toBeInTheDocument();
  });
});
