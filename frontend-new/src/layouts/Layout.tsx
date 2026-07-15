import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, Mic, Sparkles } from 'lucide-react';

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  const location = useLocation();

  const navigation = [
    { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    { name: 'New Interview', href: '/interviews/new', icon: PlusCircle },
    { name: 'New Copilot', href: '/copilots/new', icon: Sparkles },
  ];

  return (
    <div className="min-h-screen bg-white flex">
      {/* Sidebar */}
      <aside className="w-64 border-r border-border-gray bg-secondary flex flex-col">
        {/* Logo / Brand */}
        <div className="h-16 px-6 border-b border-border-gray flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center text-white">
            <Mic className="h-4 w-4" />
          </div>
          <span className="font-semibold text-primary text-sm tracking-tight">AI Interviewer</span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-4 py-6 space-y-1.5">
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
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <header className="h-16 border-b border-border-gray px-8 flex items-center justify-between">
          <div className="text-xs font-semibold text-muted-gray uppercase tracking-wider">
            {location.pathname === '/' ? 'Overview' : location.pathname.includes('/new') ? 'Session Setup' : location.pathname.includes('/copilots/') ? 'Copilot Room' : 'Interview Room'}
          </div>
        </header>

        {/* Content Body */}
        <main className="flex-1 p-8 overflow-y-auto max-w-5xl w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
};
