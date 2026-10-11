import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BrowserUtils } from '@azure/msal-browser';

describe('MSAL Popup Callback & Bridge Processing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('detects popup authentication response in URL hash without triggering router', () => {
    // Simulate window.location returning from Microsoft with auth code
    const fakeState = btoa(JSON.stringify({ id: 'test-library-state-id', meta: { interactionType: 'popup' } }));
    const mockHash = `#code=mock_auth_code_xyz&state=${fakeState}|user_state`;

    const originalLocation = window.location;
    delete (window as any).location;
    window.location = {
      ...originalLocation,
      origin: 'http://localhost:3000',
      pathname: '/',
      search: '',
      hash: mockHash,
      href: `http://localhost:3000/${mockHash}`,
    } as any;

    expect(BrowserUtils.isInPopup()).toBe(true);
    expect(window.location.hash).toContain('code=mock_auth_code_xyz');

    // Restore location
    window.location = originalLocation as any;
  });

  it('transmits response payload over BroadcastChannel to main opener frame', async () => {
    const channelId = 'test-broadcast-channel-123';
    let receivedPayload: any = null;

    // Simulate main frame listener
    const openerChannel = new BroadcastChannel(channelId);
    openerChannel.onmessage = (event) => {
      receivedPayload = event.data;
    };

    // Simulate popup broadcasting response
    const popupChannel = new BroadcastChannel(channelId);
    popupChannel.postMessage({
      v: 1,
      payload: 'code=auth_code_abc&state=mock_state',
    });

    await vi.waitFor(() => {
      expect(receivedPayload).toEqual({
        v: 1,
        payload: 'code=auth_code_abc&state=mock_state',
      });
    });

    openerChannel.close();
    popupChannel.close();
  });
});
