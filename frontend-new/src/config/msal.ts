import { LogLevel, PublicClientApplication } from '@azure/msal-browser';
import type { Configuration } from '@azure/msal-browser';

export interface MicrosoftConfig {
  clientId: string;
  tenantId: string;
  redirectUri: string;
  apiScope: string;
}

let runtimeConfig: Partial<MicrosoftConfig> | null = null;
let activeMsalInstance: PublicClientApplication | null = null;

export const setRuntimeMicrosoftConfig = (config: Partial<MicrosoftConfig>) => {
  runtimeConfig = { ...runtimeConfig, ...config };
  if (runtimeConfig.clientId && runtimeConfig.tenantId) {
    try {
      activeMsalInstance = new PublicClientApplication(createMsalConfiguration());
      msalInitPromise = null;
    } catch (e) {
      console.warn('[MSAL] Failed to re-instantiate PublicClientApplication with runtime config:', e);
    }
  }
};

export const getMicrosoftConfig = (): MicrosoftConfig => {
  const clientId = (runtimeConfig?.clientId || import.meta.env.VITE_MICROSOFT_CLIENT_ID || '').trim();
  const tenantId = (runtimeConfig?.tenantId || import.meta.env.VITE_MICROSOFT_TENANT_ID || '').trim();
  const redirectUri = (
    runtimeConfig?.redirectUri ||
    import.meta.env.VITE_MICROSOFT_REDIRECT_URL ||
    (typeof window !== 'undefined' ? window.location.origin : '')
  ).trim();
  const apiScope = (
    runtimeConfig?.apiScope ||
    import.meta.env.VITE_MICROSOFT_API_SCOPE ||
    (clientId ? `api://${clientId}/access_as_user` : '')
  ).trim();

  return {
    clientId,
    tenantId,
    redirectUri,
    apiScope,
  };
};

export const isMicrosoftSsoConfigured = (): boolean => {
  const { clientId, tenantId } = getMicrosoftConfig();
  return Boolean(clientId && tenantId);
};

export const createMsalConfiguration = (): Configuration => {
  const { clientId, tenantId, redirectUri } = getMicrosoftConfig();

  return {
    auth: {
      clientId: clientId || '00000000-0000-0000-0000-000000000000',
      authority: tenantId
        ? `https://login.microsoftonline.com/${tenantId}`
        : 'https://login.microsoftonline.com/common',
      redirectUri: redirectUri || (typeof window !== 'undefined' ? window.location.origin : ''),
      postLogoutRedirectUri: typeof window !== 'undefined' ? window.location.origin : '',
    },
    cache: {
      cacheLocation: 'sessionStorage',
    },
    system: {
      loggerOptions: {
        loggerCallback: (level, message, containsPii) => {
          if (containsPii) return;
          if (level === LogLevel.Error) {
            console.error('[MSAL]', message);
          }
        },
        logLevel: LogLevel.Error,
      },
    },
  };
};

// Single shared PublicClientApplication instance across the entire application
export const msalInstance = new PublicClientApplication(createMsalConfiguration());

let msalInitPromise: Promise<PublicClientApplication> | null = null;

/**
 * Ensures MSAL instance initialization and redirect promise handling have both completed.
 * Awaiting handleRedirectPromise() is critical to clearing any pending redirect interaction in sessionStorage.
 */
export const ensureMsalInitialized = async (): Promise<PublicClientApplication> => {
  const instance = activeMsalInstance || msalInstance;
  if (!msalInitPromise) {
    msalInitPromise = (async () => {
      try {
        await instance.initialize();
      } catch (err) {
        console.error('[MSAL] Failed to initialize PublicClientApplication:', err);
      }

      try {
        // Crucial: handleRedirectPromise must be awaited to resolve any in-flight redirect interaction
        await instance.handleRedirectPromise();
      } catch (err) {
        console.warn('[MSAL] handleRedirectPromise warning/error:', err);
      }

      return instance;
    })();
  }
  return msalInitPromise;
};

// Auto-trigger initialization on module load in browser environments
if (typeof window !== 'undefined') {
  ensureMsalInitialized().catch((err) => {
    console.error('[MSAL] Auto-initialization error:', err);
  });
}

/**
 * Backward-compatible helper that returns the shared initialized MSAL instance.
 */
export const getMsalInstance = async (): Promise<PublicClientApplication> => {
  return ensureMsalInitialized();
};

export const getLoginRequest = () => {
  const { apiScope } = getMicrosoftConfig();
  const scopes = ['openid', 'profile', 'email'];
  if (apiScope && !scopes.includes(apiScope)) {
    scopes.push(apiScope);
  }
  return {
    scopes,
    prompt: 'select_account',
  };
};
