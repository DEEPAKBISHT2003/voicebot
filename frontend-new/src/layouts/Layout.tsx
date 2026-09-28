import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, Sparkles, BarChart3, Users, LogOut, User as UserIcon } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  const location = useLocation();
  const { user, logout } = useAuth();

  // Base navigation visible to all authenticated roles (USER & ADMIN)
  const baseNavigation = [
    { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    { name: 'New Interview', href: '/interviews/new', icon: PlusCircle },
    { name: 'New Copilot', href: '/copilots/new', icon: Sparkles },
  ];

  // Admin-only navigation items: Analytics (Usage & Costs) and User Control
  const adminNavigation = [
    { name: 'Analytics', href: '/analytics', icon: BarChart3 },
    { name: 'User Control', href: '/user-control', icon: Users },
  ];

  const navigation = user?.role === 'ADMIN' ? [...baseNavigation, ...adminNavigation] : baseNavigation;

  return (
    <div className="min-h-screen bg-white flex">
      {/* Sidebar */}
      <aside className="w-64 border-r border-border-gray bg-secondary flex flex-col justify-between">
        <div>
          {/* Logo / Brand */}
          <div className="h-16 px-6 border-b border-border-gray flex items-center gap-2.5">
            <img src="/appzlogo.webp" alt="Appz Logo" className="h-8 w-8 object-contain" />
            <span className="font-semibold text-primary text-sm tracking-tight">Appz Interviewer</span>
          </div>

          {/* Navigation */}
          <nav className="px-4 py-6 space-y-1.5">
            {navigation.map((item) => {
              const isActive = location.pathname === item.href;
              return (
                <Link
                  key={item.name}
                  to={item.href}
                  className={`flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-lg transition-colors ${isActive
                      ? 'bg-primary text-white'
                      : 'text-muted-gray hover:bg-border-gray/30 hover:text-primary'
                    }`}
                >
                  <item.icon className="h-4 w-4 shrink-0" />
                  {item.name}
                </Link>
              );
            })}
          </nav>
        </div>

        {/* User Card in Sidebar Footer */}
        {user && (
          <div className="p-4 border-t border-border-gray">
            <div className="flex items-center gap-3 mb-3">
              <div className="h-9 w-9 rounded-full bg-slate-200 flex items-center justify-center shrink-0">
                <UserIcon className="h-4 w-4 text-slate-600" />
              </div>
              <div className="overflow-hidden">
                <p className="text-xs font-semibold text-slate-800 truncate" title={user.email}>
                  {user.email}
                </p>
                <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded tracking-wide uppercase ${
                  user.role === 'ADMIN' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-600'
                }`}>
                  {user.role}
                </span>
              </div>
            </div>
            <button
              onClick={() => logout()}
              className="w-full flex items-center justify-center gap-2 px-3 py-1.5 text-xs font-medium text-red-600 bg-red-50 hover:bg-red-100 rounded-lg transition-colors"
            >
              <LogOut className="h-3.5 w-3.5" />
              Sign Out
            </button>
          </div>
        )}
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <header className="h-16 border-b border-border-gray px-8 flex items-center justify-between">
          <div className="text-xs font-semibold text-muted-gray uppercase tracking-wider">
            {location.pathname === '/'
              ? 'Overview'
              : location.pathname.includes('/new')
              ? 'Session Setup'
              : location.pathname.includes('/copilots/')
              ? 'Appz Moderator Room'
              : location.pathname === '/analytics' || location.pathname === '/usage'
              ? 'Analytics - Usage & Costs'
              : location.pathname === '/user-control'
              ? 'User Control'
              : 'Interview Room'}
          </div>

          {user && (
            <div className="flex items-center gap-3 text-xs text-slate-500">
              <span>Signed in as <strong className="text-slate-800">{user.email}</strong></span>
              <span className={`font-bold px-2 py-0.5 rounded uppercase text-[10px] ${
                user.role === 'ADMIN' ? 'bg-indigo-50 text-indigo-600 border border-indigo-200' : 'bg-slate-100 text-slate-600'
              }`}>
                {user.role}
              </span>
            </div>
          )}
        </header>

        {/* Content Body */}
        <main className="flex-1 p-8 overflow-y-auto max-w-5xl w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
};
