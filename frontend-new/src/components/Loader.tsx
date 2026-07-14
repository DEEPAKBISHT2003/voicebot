import React from 'react';
import { Loader2 } from 'lucide-react';

interface LoaderProps {
  label?: string;
  className?: string;
}

export const Loader: React.FC<LoaderProps> = ({ label = 'Loading...', className = '' }) => {
  return (
    <div className={`flex flex-col items-center justify-center p-8 text-muted-gray text-sm gap-3 ${className}`}>
      <Loader2 className="h-8 w-8 animate-spin text-primary" />
      {label && <span>{label}</span>}
    </div>
  );
};

export const Skeleton: React.FC<{ className?: string }> = ({ className = '' }) => {
  return (
    <div className={`animate-pulse rounded bg-gray-200 ${className}`} />
  );
};
