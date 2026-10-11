import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import api from '../api/axios';
import { Lock, Mail, AlertCircle, Loader2 } from 'lucide-react';
import { useMsal, MsalContext } from '@azure/msal-react';
import { InteractionStatus } from '@azure/msal-browser';
import {
  isMicrosoftSsoConfigured,
  ensureMsalInitialized,
  getLoginRequest,
  getMicrosoftConfig,
} from '../config/msal';

export const Login: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isMicrosoftSubmitting, setIsMicrosoftSubmitting] = useState(false);

  const { instance: msalInstance, inProgress } = useMsal();
  const inFlightRef = useRef(false);

  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const isMsConfigured = isMicrosoftSsoConfigured();
  const isMsalBusy = inProgress !== InteractionStatus.None;
  const from = (location.state as any)?.from?.pathname || '/';

  useEffect(() => {
    // If already authenticated, redirect to target
    if (isAuthenticated) {
      navigate(from, { replace: true });
    }

    // Check for session expiry notice
    const notice = sessionStorage.getItem('auth_expired_notice');
    if (notice) {
      setSessionNotice(notice);
      sessionStorage.removeItem('auth_expired_notice');
    }
  }, [isAuthenticated, navigate, from]);

  useEffect(() => {
    return () => {
      inFlightRef.current = false;
    };
  }, []);

  const handleMicrosoftLogin = async () => {
    setErrorMessage(null);
    setSessionNotice(null);

    if (!isMsConfigured) {
      setErrorMessage(
        'Microsoft SSO is currently not configured on this environment. Please sign in using your email and password.'
      );
      return;
    }

    // Synchronous in-flight guard to prevent duplicate clicks immediately
    if (inFlightRef.current) {
      return;
    }

    // Check if MSAL reports an interaction in progress
    if (inProgress !== InteractionStatus.None) {
      setErrorMessage(
        'A Microsoft sign-in operation is currently in progress. Please complete or close the existing window and try again.'
      );
      return;
    }

    inFlightRef.current = true;
    setIsMicrosoftSubmitting(true);

    try {
      // Resolve active PCA directly to preserve synchronous user gesture activation
      const isStubbedPca =
        !msalInstance || msalInstance === (MsalContext as any)?._currentValue?.instance;
      const activePca = !isStubbedPca ? msalInstance : await ensureMsalInitialized();

      const loginRequest = getLoginRequest();

      // Launch MSAL popup flow
      const response = await activePca.loginPopup(loginRequest);

      let accessToken = response.accessToken;
      const { apiScope } = getMicrosoftConfig();

      // If accessToken is missing and an API scope is configured, acquire token silently or via popup
      if (!accessToken && apiScope) {
        try {
          const silentRes = await activePca.acquireTokenSilent({
            scopes: [apiScope],
            account: response.account,
          });
          accessToken = silentRes?.accessToken;
        } catch (silentErr) {
          console.warn('[MSAL] Silent token acquisition failed, attempting interactive popup:', silentErr);
          try {
            const popupRes = await activePca.acquireTokenPopup({
              scopes: [apiScope],
              account: response.account,
            });
            accessToken = popupRes?.accessToken;
          } catch (popupErr) {
            console.warn('[MSAL] Interactive token acquisition failed:', popupErr);
          }
        }
      }

      // Enforce valid access token; do not fall back to unprivileged ID tokens
      const token = (accessToken || '').trim();
      if (!token) {
        throw new Error(
          'Unable to acquire Microsoft API access token. Please verify that application permissions are consented in Microsoft Entra ID.'
        );
      }

      // Exchange Microsoft token for platform JWT
      const backendRes = await api.post('/auth/microsoft', {
        access_token: token,
      });

      const { access_token, role } = backendRes.data;

      // Store platform JWT
      localStorage.setItem('voicebot_token', access_token);

      // Retrieve authenticated user profile
      const meResponse = await api.get('/auth/me', {
        headers: {
          Authorization: `Bearer ${access_token}`,
        },
      });

      login(
        access_token,
        role,
        meResponse.data.email,
        meResponse.data.id,
        meResponse.data.name
      );
      navigate(from, { replace: true });
    } catch (err: any) {
      // Check for user cancellation in MSAL popup
      const errorCode = err?.errorCode || err?.name || '';
      const isCancelled =
        errorCode === 'user_cancelled' ||
        errorCode.toLowerCase().includes('user_cancelled') ||
        err?.message?.includes('user_cancelled') ||
        err?.message?.includes('User cancelled');

      if (isCancelled) {
        return; // Silently reset loading state
      }

      // Handle interaction in progress specifically
      if (
        errorCode === 'interaction_in_progress' ||
        err?.message?.includes('interaction_in_progress')
      ) {
        setErrorMessage(
          'A Microsoft sign-in operation is currently in progress. Please complete or close the sign-in window and try again.'
        );
        return;
      }

      const correlationId = err?.correlationId || '';
      console.warn(
        `[Auth-Microsoft] Sign-in failed (code: ${errorCode || 'unknown'}, correlationId: ${correlationId || 'none'})`
      );

      // Handle popup window blocked or closed by user / timeout
      if (
        errorCode === 'popup_window_error' ||
        errorCode === 'empty_window_error' ||
        errorCode === 'monitor_window_timeout' ||
        errorCode === 'timed_out' ||
        err?.message?.includes('timed_out') ||
        err?.message?.includes('monitor_window_timeout')
      ) {
        setErrorMessage(
          'Microsoft sign-in window was closed or timed out. Please try signing in again.'
        );
        return;
      }

      if (err.response?.status === 401) {
        setErrorMessage(
          err.response?.data?.detail || 'Invalid or expired Microsoft authentication credentials.'
        );
      } else if (err.response?.status === 403) {
        setErrorMessage(
          err.response?.data?.detail || 'Access denied: account is disabled or unauthorized.'
        );
      } else if (err.response?.status === 409) {
        setErrorMessage(
          err.response?.data?.detail ||
            'An account with this email already exists. Please sign in with email and password.'
        );
      } else if (err.response?.status === 503) {
        setErrorMessage(
          'Microsoft SSO is currently unavailable or not configured on the backend.'
        );
      } else if (err.response?.status === 422) {
        const detail = err.response?.data?.detail;
        const msg = Array.isArray(detail)
          ? detail.map((d: any) => d.msg || JSON.stringify(d)).join('; ')
          : typeof detail === 'string'
          ? detail
          : 'Invalid authentication request payload.';
        setErrorMessage(`Authentication error: ${msg}`);
      } else if (err.response?.data?.detail) {
        setErrorMessage(
          typeof err.response.data.detail === 'string'
            ? err.response.data.detail
            : Array.isArray(err.response.data.detail)
            ? err.response.data.detail.map((d: any) => d.msg || JSON.stringify(d)).join('; ')
            : 'Authentication failed. Please try again.'
        );
      } else {
        setErrorMessage(
          err.message || 'Failed to authenticate with Microsoft. Please try again.'
        );
      }
    } finally {
      inFlightRef.current = false;
      setIsMicrosoftSubmitting(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setSessionNotice(null);

    const cleanEmail = email.trim();
    if (!cleanEmail || !password) {
      setErrorMessage('Please enter both email and password.');
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await api.post('/auth/login', {
        email: cleanEmail,
        password: password,
      });

      const { access_token, role } = response.data;
      
      // Store token and query user details to restore state
      localStorage.setItem('voicebot_token', access_token);
      
      const meResponse = await api.get('/auth/me', {
        headers: {
          Authorization: `Bearer ${access_token}`,
        },
      });

      login(access_token, role, meResponse.data.email, meResponse.data.id);
      navigate(from, { replace: true });
    } catch (err: any) {
      if (err.response?.status === 401) {
        setErrorMessage('Invalid email or password.');
      } else if (err.response?.status === 403) {
        setErrorMessage('This account is disabled. Please contact an administrator.');
      } else if (err.response?.data?.detail) {
        setErrorMessage(
          typeof err.response.data.detail === 'string'
            ? err.response.data.detail
            : 'Authentication failed. Please check your credentials.'
        );
      } else {
        setErrorMessage('Failed to connect to authentication server. Please try again.');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="flex justify-center">
          <img src="/appzlogo.webp" alt="Appz Logo" className="h-12 w-12 object-contain" />
        </div>
        <h2 className="mt-4 text-center text-2xl font-bold tracking-tight text-slate-900">
          Sign in to Appz Interview Platform
        </h2>
        <p className="mt-1 text-center text-sm text-slate-600">
          Enter your credentials to access your session workspace
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-white py-8 px-4 shadow-sm border border-slate-200 sm:rounded-xl sm:px-10">
          {sessionNotice && (
            <div className="mb-5 rounded-lg bg-amber-50 p-3.5 border border-amber-200 flex items-start gap-2.5">
              <AlertCircle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
              <p className="text-sm text-amber-800">{sessionNotice}</p>
            </div>
          )}

          {errorMessage && (
            <div className="mb-5 rounded-lg bg-red-50 p-3.5 border border-red-200 flex items-start gap-2.5">
              <AlertCircle className="h-5 w-5 text-red-600 shrink-0 mt-0.5" />
              <p className="text-sm text-red-800">{errorMessage}</p>
            </div>
          )}

          {/* Microsoft SSO Action */}
          <div>
            <button
              type="button"
              onClick={handleMicrosoftLogin}
              disabled={isMicrosoftSubmitting || isSubmitting || isMsalBusy}
              className={`w-full flex justify-center items-center py-2.5 px-4 border rounded-lg shadow-xs text-sm font-semibold transition-colors ${
                isMsConfigured && !isMsalBusy
                  ? 'bg-white hover:bg-slate-50 text-slate-700 hover:text-slate-900 border-slate-300 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-slate-500'
                  : 'bg-slate-50 text-slate-400 cursor-not-allowed border-slate-200'
              }`}
              title={
                !isMsConfigured
                  ? 'Microsoft SSO is currently not configured on this environment'
                  : isMsalBusy
                  ? 'Sign-in operation is in progress. Please wait...'
                  : 'Sign in with your Appzlogic Microsoft Entra account'
              }
            >
              {isMicrosoftSubmitting || isMsalBusy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2 text-slate-500" />
                  Connecting to Microsoft...
                </>
              ) : (
                <>
                  <svg className="h-4 w-4 mr-2.5 shrink-0" viewBox="0 0 21 21">
                    <rect x="1" y="1" width="9" height="9" fill="#f25022" />
                    <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
                    <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
                    <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
                  </svg>
                  <span>Sign in with Microsoft</span>
                  {!isMsConfigured && (
                    <span className="ml-2 text-xs font-normal text-slate-400">
                      (Unavailable)
                    </span>
                  )}
                </>
              )}
            </button>
          </div>

          <div className="relative my-6">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-slate-200" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-white px-3 text-slate-500 font-medium tracking-wide">
                Or sign in with email
              </span>
            </div>
          </div>

          <form className="space-y-5" onSubmit={handleSubmit}>
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700">
                Email Address
              </label>
              <div className="mt-1.5 relative rounded-md shadow-sm">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Mail className="h-4 w-4 text-slate-400" />
                </div>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@company.com"
                  className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600"
                />
              </div>
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-slate-700">
                Password
              </label>
              <div className="mt-1.5 relative rounded-md shadow-sm">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Lock className="h-4 w-4 text-slate-400" />
                </div>
                <input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600"
                />
              </div>
            </div>

            <div>
              <button
                type="submit"
                disabled={isSubmitting}
                className="w-full flex justify-center items-center py-2.5 px-4 border border-transparent rounded-lg shadow-sm text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 transition-colors"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    Signing in...
                  </>
                ) : (
                  'Sign In'
                )}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};
