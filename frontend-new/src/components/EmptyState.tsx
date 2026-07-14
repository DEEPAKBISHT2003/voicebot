import React from 'react';
import { HelpCircle } from 'lucide-react';
import { Button } from './Button';

interface EmptyStateProps {
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: React.ReactNode;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title,
  description,
  actionLabel,
  onAction,
  icon,
}) => {
  return (
    <div className="flex flex-col items-center justify-center text-center p-12 border border-dashed border-border-gray rounded-lg bg-secondary">
      <div className="h-12 w-12 rounded-full border border-border-gray bg-white flex items-center justify-center mb-4 text-muted-gray">
        {icon || <HelpCircle className="h-6 w-6" />}
      </div>
      <h3 className="text-base font-semibold text-primary mb-1">{title}</h3>
      <p className="text-sm text-muted-gray max-w-sm mb-6">{description}</p>
      {actionLabel && onAction && (
        <Button variant="primary" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
};
