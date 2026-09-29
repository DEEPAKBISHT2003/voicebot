import React from 'react';

export const AnalyticsPlaceholder: React.FC = () => {
  return (
    <div className="p-8">
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-xl font-bold text-slate-900 mb-2">Analytics</h1>
        <p className="text-sm text-slate-500">
          Platform analytics and reporting module is upcoming in a future release.
        </p>
      </div>
    </div>
  );
};

export const UserControlPlaceholder: React.FC = () => {
  return (
    <div className="p-8">
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-xl font-bold text-slate-900 mb-2">User Control</h1>
        <p className="text-sm text-slate-500">
          User management UI is upcoming. For Phase 1, user management APIs are accessible via Swagger (/docs) and Postman.
        </p>
      </div>
    </div>
  );
};
