import React, { forwardRef } from 'react';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, className = '', id, ...props }, ref) => {
    return (
      <div className="w-full flex flex-col gap-1.5">
        {label && (
          <label htmlFor={id} className="text-xs font-semibold text-primary">
            {label}
          </label>
        )}
        <input
          id={id}
          ref={ref}
          className={`flex h-10 w-full rounded-lg border border-border-gray bg-white px-3 py-2 text-sm text-primary placeholder:text-muted-gray focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50 ${
            error ? 'border-red-500 focus-visible:outline-red-500' : ''
          } ${className}`}
          {...props}
        />
        {error && <span className="text-xs text-red-500">{error}</span>}
      </div>
    );
  }
);

Input.displayName = 'Input';
