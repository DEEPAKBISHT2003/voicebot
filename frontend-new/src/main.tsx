import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.tsx';
import { BrowserUtils } from '@azure/msal-browser';
import { broadcastResponseToMainFrame } from '@azure/msal-browser/redirect-bridge';

// Check if this window was opened as an MSAL popup or iframe for auth response processing
const isMsalAuthPopup =
  typeof window !== 'undefined' &&
  (BrowserUtils.isInPopup() ||
    BrowserUtils.isInIframe() ||
    (Boolean(window.opener) &&
      window.opener !== window &&
      (window.location.hash.includes('code=') ||
        window.location.search.includes('code=') ||
        window.location.hash.includes('error=') ||
        window.location.search.includes('error='))));

if (isMsalAuthPopup) {
  // Render a minimal processing UI while MSAL completes the exchange with window.opener.
  // Crucial: do NOT mount the full React Router app here, as ProtectedRoute would redirect
  // to /login and clear the authorization code/hash from the URL before it can be processed.
  const root = document.getElementById('root');
  if (root) {
    root.innerHTML = `
      <div style="min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #334155;">
        <div style="width: 36px; height: 36px; border: 3px solid #6366f1; border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px;"></div>
        <p style="font-size: 15px; font-weight: 600; margin: 0;">Completing Microsoft authentication...</p>
        <p style="font-size: 13px; color: #64748b; margin-top: 6px;">This window will close automatically.</p>
        <style>
          @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        </style>
      </div>
    `;
  }

  // Broadcast the authentication response from this popup/iframe window to the main opener frame.
  // MSAL's waitForBridgeResponse in the opener listens on BroadcastChannel(libraryState.id)
  // for this broadcast to complete token acquisition.
  broadcastResponseToMainFrame()
    .then(() => {
      setTimeout(() => {
        if (!window.closed) {
          try {
            window.close();
          } catch {
            // Ignore window.close browser restrictions
          }
          if (!window.closed && root) {
            const p = root.querySelector('p');
            if (p) {
              p.textContent = 'Authentication complete. You can close this window.';
            }
          }
        }
      }, 500);
    })
    .catch((err) => {
      console.error('[MSAL] Error broadcasting popup response to main frame:', err);
    });
} else {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
}
