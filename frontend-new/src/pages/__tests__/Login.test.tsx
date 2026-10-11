import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { InteractionStatus } from '@azure/msal-browser';
import { Login } from '../Login';
import { AuthProvider } from '../../context/AuthContext';
import api from '../../api/axios';
import * as msalModule from '../../config/msal';

// Mock axios API
vi.mock('../../api/axios', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
    create: vi.fn().mockReturnThis(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}));

let mockInProgress: InteractionStatus = InteractionStatus.None;
const mockMsalInstance = {
  loginPopup: vi.fn(),
  acquireTokenSilent: vi.fn(),
  acquireTokenPopup: vi.fn(),
  initialize: vi.fn().mockResolvedValue(undefined),
  handleRedirectPromise: vi.fn().mockResolvedValue(null),
};

vi.mock('@azure/msal-react', async () => {
  const actual = await vi.importActual<any>('@azure/msal-react');
  return {
    ...actual,
    useMsal: () => ({
      instance: mockMsalInstance,
      inProgress: mockInProgress,
      accounts: [],
    }),
  };
});

describe('Login Page Authentication Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mockInProgress = InteractionStatus.None;

    vi.spyOn(msalModule, 'ensureMsalInitialized').mockResolvedValue(mockMsalInstance as any);
    vi.spyOn(msalModule, 'getMsalInstance').mockResolvedValue(mockMsalInstance as any);
  });

  const renderLoginPage = () => {
    return render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<div>Dashboard Home</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );
  };

  it('renders both Microsoft SSO button and email/password form', () => {
    renderLoginPage();

    expect(screen.getByText('Sign in to Appz Interview Platform')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign in with Microsoft/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Email Address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Sign In$/i })).toBeInTheDocument();
  });

  it('displays configuration notice when Microsoft SSO is not configured', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(false);

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    expect(msButton).toBeInTheDocument();
    expect(screen.getByText('(Unavailable)')).toBeInTheDocument();

    fireEvent.click(msButton);

    await waitFor(() => {
      expect(screen.getByText(/Microsoft SSO is currently not configured/i)).toBeInTheDocument();
    });
  });

  it('successful Microsoft SSO flow acquires token, exchanges for platform JWT, and logs in', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);

    mockMsalInstance.loginPopup.mockResolvedValue({
      accessToken: 'mock-microsoft-access-token-xyz',
      account: { username: 'john@appzlogic.com' },
    });

    // Mock backend responses
    (api.post as any).mockImplementation((url: string) => {
      if (url === '/auth/microsoft') {
        return Promise.resolve({
          data: {
            access_token: 'platform-jwt-token-abc',
            token_type: 'bearer',
            role: 'USER',
            session_id: 'session-uuid-1',
          },
        });
      }
      return Promise.reject(new Error(`Unhandled POST ${url}`));
    });

    (api.get as any).mockImplementation((url: string) => {
      if (url === '/auth/me') {
        return Promise.resolve({
          data: {
            id: 'local-user-id-123',
            email: 'john@appzlogic.com',
            name: 'John Doe',
            role: 'USER',
          },
        });
      }
      return Promise.reject(new Error(`Unhandled GET ${url}`));
    });

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(mockMsalInstance.loginPopup).toHaveBeenCalledTimes(1);
      expect(api.post).toHaveBeenCalledWith('/auth/microsoft', {
        access_token: 'mock-microsoft-access-token-xyz',
      });
      expect(localStorage.getItem('voicebot_token')).toBe('platform-jwt-token-abc');
      expect(screen.getByText('Dashboard Home')).toBeInTheDocument();
    });
  });

  it('handles user cancellation of Microsoft popup gracefully without error state', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);

    mockMsalInstance.loginPopup.mockRejectedValue({
      errorCode: 'user_cancelled',
      message: 'User cancelled the flow',
    });

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(mockMsalInstance.loginPopup).toHaveBeenCalledTimes(1);
      expect(msButton).not.toBeDisabled();
      expect(screen.queryByText(/Authentication failed/i)).not.toBeInTheDocument();
    });
  });

  it('prevents duplicate concurrent clicks using inFlightRef guard', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);

    let rejectPopup: (err: any) => void;
    mockMsalInstance.loginPopup.mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectPopup = reject;
        })
    );

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    // First click initiates flow
    fireEvent.click(msButton);
    // Rapid duplicate clicks should be blocked immediately
    fireEvent.click(msButton);
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(mockMsalInstance.loginPopup).toHaveBeenCalledTimes(1);
    });

    // Reject the promise safely within act simulating user cancellation
    await act(async () => {
      rejectPopup!({
        errorCode: 'user_cancelled',
        message: 'User cancelled',
      });
    });
  });

  it('disables Microsoft button when MSAL reports interaction is already in progress', () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);
    mockInProgress = InteractionStatus.AcquireToken;

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Connecting to Microsoft\.\.\./i });
    expect(msButton).toBeDisabled();
    expect(mockMsalInstance.loginPopup).not.toHaveBeenCalled();
  });

  it('handles interaction_in_progress error thrown by MSAL gracefully with user notification', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);

    mockMsalInstance.loginPopup.mockRejectedValue({
      errorCode: 'interaction_in_progress',
      message: 'Interaction is currently in progress',
    });

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(
        screen.getByText(
          /A Microsoft sign-in operation is currently in progress\. Please complete or close the sign-in window and try again\./i
        )
      ).toBeInTheDocument();
    });
  });

  it('handles timed_out error gracefully when popup is closed or bridge times out', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);

    mockMsalInstance.loginPopup.mockRejectedValue({
      errorCode: 'timed_out',
      message: 'redirect_bridge_timeout',
    });

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(
        screen.getByText(
          /Microsoft sign-in window was closed or timed out\. Please try signing in again\./i
        )
      ).toBeInTheDocument();
    });
  });

  it('sanitizes error logging without leaking tokens or credential parameters', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);
    const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    mockMsalInstance.loginPopup.mockRejectedValue({
      errorCode: 'timed_out',
      correlationId: 'test-correlation-uuid-1234',
      message: 'sensitive_internal_url#code=secret123&state=xyz',
    });

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(consoleWarnSpy).toHaveBeenCalledWith(
        '[Auth-Microsoft] Sign-in failed (code: timed_out, correlationId: test-correlation-uuid-1234)'
      );
    });

    consoleWarnSpy.mockRestore();
  });

  it('displays conflict message when backend rejects unlinked account (409)', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);

    mockMsalInstance.loginPopup.mockResolvedValue({
      accessToken: 'mock-ms-token',
      account: {},
    });

    (api.post as any).mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: 'An account with this email address already exists. Automatic linking is disabled.',
        },
      },
    });

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(
        screen.getByText(/An account with this email address already exists/i)
      ).toBeInTheDocument();
    });
  });

  it('displays disabled error message when user account is inactive (403)', async () => {
    vi.spyOn(msalModule, 'isMicrosoftSsoConfigured').mockReturnValue(true);

    mockMsalInstance.loginPopup.mockResolvedValue({
      accessToken: 'mock-ms-token',
      account: {},
    });

    (api.post as any).mockRejectedValue({
      response: {
        status: 403,
        data: {
          detail: 'This account is disabled. Please contact an administrator.',
        },
      },
    });

    renderLoginPage();

    const msButton = screen.getByRole('button', { name: /Sign in with Microsoft/i });
    fireEvent.click(msButton);

    await waitFor(() => {
      expect(
        screen.getByText(/This account is disabled\. Please contact an administrator\./i)
      ).toBeInTheDocument();
    });
  });

  it('preserves email/password login functionality', async () => {
    (api.post as any).mockImplementation((url: string) => {
      if (url === '/auth/login') {
        return Promise.resolve({
          data: {
            access_token: 'pwd-jwt-token-xyz',
            role: 'USER',
          },
        });
      }
      return Promise.reject(new Error(`Unhandled POST ${url}`));
    });

    (api.get as any).mockImplementation((url: string) => {
      if (url === '/auth/me') {
        return Promise.resolve({
          data: {
            id: 'pwd-user-id',
            email: 'standard@appzlogic.com',
            name: 'Standard User',
            role: 'USER',
          },
        });
      }
      return Promise.reject(new Error(`Unhandled GET ${url}`));
    });

    renderLoginPage();

    fireEvent.change(screen.getByLabelText(/Email Address/i), {
      target: { value: 'standard@appzlogic.com' },
    });
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: 'SecretPassword123!' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Sign In$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/auth/login', {
        email: 'standard@appzlogic.com',
        password: 'SecretPassword123!',
      });
      expect(localStorage.getItem('voicebot_token')).toBe('pwd-jwt-token-xyz');
      expect(screen.getByText('Dashboard Home')).toBeInTheDocument();
    });
  });
});
