import React, { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  PlusCircle,
  Video,
  BarChart3,
  Users,
  LogOut,
  User as UserIcon,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  const location = useLocation();
  const { user, logout } = useAuth();

  // Collapsible sidebar state with localStorage persistence
  const [isCollapsed, setIsCollapsed] = useState<boolean>(() => {
    return localStorage.getItem('sidebar_collapsed') === 'true';
  });

  const toggleSidebar = () => {
    setIsCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem('sidebar_collapsed', String(next));
      return next;
    });
  };

  // Base navigation visible to all authenticated roles (USER & ADMIN)
  const baseNavigation = [
    { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    { name: 'New Interview', href: '/interviews/new', icon: PlusCircle },
    { name: 'Meeting Observer', href: '/meeting-observer', icon: Video },
    // { name: 'New Copilot', href: '/copilots/new', icon: Sparkles },
  ];

  // Admin-only navigation items: Analytics (Usage & Costs) and User Control
  const adminNavigation = [
    { name: 'Analytics', href: '/analytics', icon: BarChart3 },
    { name: 'User Control', href: '/user-control', icon: Users },
  ];

  const navigation = user?.role === 'ADMIN' ? [...baseNavigation, ...adminNavigation] : baseNavigation;

  return (
    <div className="h-screen w-screen overflow-hidden bg-white flex">
      {/* Sidebar: Fixed height, sticky top/bottom, scrollable middle, collapsible width */}
      <aside
        className={`${
          isCollapsed ? 'w-20' : 'w-64'
        } shrink-0 h-full border-r border-border-gray bg-secondary flex flex-col justify-between transition-all duration-300 ease-in-out z-20`}
      >
        {/* Top Header / Brand */}
        <div className="h-16 px-4 border-b border-border-gray flex items-center justify-between shrink-0">
          <Link to="/" className="flex items-center gap-2.5 overflow-hidden">
            <img src="/appzlogo.webp" alt="Appz Logo" className="h-8 w-8 object-contain shrink-0" />
            {!isCollapsed && (
              <span className="font-semibold text-primary text-sm tracking-tight truncate whitespace-nowrap">
                Appz Interviewer
              </span>
            )}
          </Link>

          {/* Collapse/Expand Toggle Button */}
          <button
            onClick={toggleSidebar}
            title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="p-1.5 rounded-lg text-muted-gray hover:text-primary hover:bg-border-gray/40 transition-colors shrink-0 ml-1"
          >
            {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        </div>

        {/* Scrollable Navigation List (only nav links scroll if window height is very small) */}
        <nav className="flex-1 px-3 py-4 space-y-1.5 overflow-y-auto overflow-x-hidden">
          {navigation.map((item) => {
            const isActive =
              item.href === '/'
                ? location.pathname === '/'
                : location.pathname === item.href || location.pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.name}
                to={item.href}
                title={isCollapsed ? item.name : undefined}
                className={`flex items-center ${
                  isCollapsed ? 'justify-center px-2 py-2.5' : 'gap-3 px-3 py-2'
                } text-sm font-medium rounded-lg transition-colors ${
                  isActive
                    ? 'bg-primary text-white'
                    : 'text-muted-gray hover:bg-border-gray/30 hover:text-primary'
                }`}
              >
                <item.icon className="h-4 w-4 shrink-0" />
                {!isCollapsed && <span className="truncate whitespace-nowrap">{item.name}</span>}
              </Link>
            );
          })}
        </nav>

        {/* User Card & Sign Out Button (Strictly pinned at bottom - never requires scrolling) */}
        {user && (
          <div className="p-3 border-t border-border-gray shrink-0 bg-secondary">
            {!isCollapsed ? (
              <>
                <div className="flex items-center gap-2.5 mb-2.5">
                  <div className="h-8 w-8 rounded-full bg-slate-200 flex items-center justify-center shrink-0">
                    <UserIcon className="h-4 w-4 text-slate-600" />
                  </div>
                  <div className="overflow-hidden min-w-0">
                    <p className="text-xs font-semibold text-slate-800 truncate" title={user.email}>
                      {user.email}
                    </p>
                    <span
                      className={`inline-block text-[10px] font-bold px-1.5 py-0.2 rounded tracking-wide uppercase ${
                        user.role === 'ADMIN' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-600'
                      }`}
                    >
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
              </>
            ) : (
              <div className="flex flex-col items-center gap-2">
                <div
                  className="h-8 w-8 rounded-full bg-slate-200 flex items-center justify-center"
                  title={`${user.email} (${user.role})`}
                >
                  <UserIcon className="h-4 w-4 text-slate-600" />
                </div>
                <button
                  onClick={() => logout()}
                  title="Sign Out"
                  className="p-2 text-red-600 hover:bg-red-100 rounded-lg transition-colors"
                >
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>
        )}
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col h-full min-w-0 overflow-hidden">
        {/* Header */}
        <header className="h-16 border-b border-border-gray px-8 flex items-center justify-between shrink-0 bg-white z-10">
          <div className="text-xs font-semibold text-muted-gray uppercase tracking-wider truncate">
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
            <div className="flex items-center gap-3 text-xs text-slate-500 shrink-0">
              <span className="hidden sm:inline">
                Signed in as <strong className="text-slate-800">{user.email}</strong>
              </span>
              <span
                className={`font-bold px-2 py-0.5 rounded uppercase text-[10px] ${
                  user.role === 'ADMIN'
                    ? 'bg-indigo-50 text-indigo-600 border border-indigo-200'
                    : 'bg-slate-100 text-slate-600'
                }`}
              >
                {user.role}
              </span>
            </div>
          )}
        </header>

        {/* Content Body */}
        <main className="flex-1 p-8 overflow-y-auto w-full">
          <div className="max-w-5xl mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
};
