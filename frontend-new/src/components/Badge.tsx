import React from 'react';

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'success' | 'warning' | 'info' | 'error' | 'default';
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  className = '',
}) => {
  const baseStyles = 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold';

  const variants = {
    success: 'bg-green-50 text-green-700 border border-green-200',
    warning: 'bg-yellow-50 text-yellow-700 border border-yellow-200',
    info: 'bg-blue-50 text-blue-700 border border-blue-200',
    error: 'bg-red-50 text-red-700 border border-red-200',
    default: 'bg-gray-100 text-gray-800 border border-gray-200',
  };

  return (
    <span className={`${baseStyles} ${variants[variant]} ${className}`}>
      {children}
    </span>
  );
};
