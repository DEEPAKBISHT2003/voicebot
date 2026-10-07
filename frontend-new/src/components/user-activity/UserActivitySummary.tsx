import React from 'react';
import { Users, Wifi, WifiOff, Monitor } from 'lucide-react';
import { Card } from '../Card';
import { Skeleton } from '../Loader';
import type { ActivitySummary } from '../../api/activity';

interface UserActivitySummaryProps {
  summary: ActivitySummary | null;
  isLoading?: boolean;
}

export const UserActivitySummary: React.FC<UserActivitySummaryProps> = ({ summary, isLoading }) => {
  if (isLoading || !summary) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} className="flex items-center gap-4 p-5">
            <Skeleton className="h-12 w-12 rounded-lg" />
            <div className="space-y-2 flex-1">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-6 w-12" />
            </div>
          </Card>
        ))}
      </div>
    );
  }

  const cards = [
    {
      title: 'Total Users',
      value: summary.total_users,
      icon: Users,
      color: 'bg-blue-50 text-blue-700 border-blue-200',
      badgeColor: 'bg-blue-100 text-blue-800',
    },
    {
      title: 'Online Users',
      value: summary.online_users,
      icon: Wifi,
      color: 'bg-emerald-50 text-emerald-700 border-emerald-200',
      badgeColor: 'bg-emerald-100 text-emerald-800',
      pulse: true,
    },
    {
      title: 'Offline Users',
      value: summary.offline_users,
      icon: WifiOff,
      color: 'bg-slate-50 text-slate-700 border-slate-200',
      badgeColor: 'bg-slate-100 text-slate-800',
    },
    {
      title: 'Active Sessions',
      value: summary.active_sessions,
      icon: Monitor,
      color: 'bg-purple-50 text-purple-700 border-purple-200',
      badgeColor: 'bg-purple-100 text-purple-800',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((card) => {
        const IconComponent = card.icon;
        return (
          <Card
            key={card.title}
            className="flex items-center justify-between p-5 border border-border-gray hover:border-slate-300 transition-all shadow-xs"
          >
            <div className="space-y-1">
              <span className="text-xs font-semibold text-muted-gray uppercase tracking-wider block">
                {card.title}
              </span>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold text-primary">{card.value}</span>
                {card.pulse && card.value > 0 && (
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
                  </span>
                )}
              </div>
            </div>
            <div className={`p-3 rounded-xl border ${card.color}`}>
              <IconComponent className="h-5 w-5" />
            </div>
          </Card>
        );
      })}
    </div>
  );
};
