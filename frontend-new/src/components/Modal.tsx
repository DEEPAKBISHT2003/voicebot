import React from 'react';
import { X } from 'lucide-react';
import { Button } from './Button';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: 'sm' | 'md' | 'lg';
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  footer,
  size = 'md',
}) => {
  if (!isOpen) return null;

  const sizes = {
    sm: 'max-w-md',
    md: 'max-w-lg',
    lg: 'max-w-2xl',
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-primary/20 backdrop-blur-xs transition-opacity"
        onClick={onClose}
      />

      {/* Content */}
      <div className={`relative w-full ${sizes[size]} transform overflow-hidden rounded-lg bg-white border border-border-gray shadow-xl transition-all`}>
        <div className="flex items-center justify-between border-b border-border-gray px-6 py-4">
          <h3 className="text-base font-semibold text-primary">{title}</h3>
          <Button variant="outline" size="sm" className="p-1 border-none bg-transparent hover:bg-secondary rounded-full" onClick={onClose}>
            <X className="h-4 w-4 text-muted-gray hover:text-primary" />
          </Button>
        </div>
        <div className="px-6 py-4 max-h-[70vh] overflow-y-auto">
          {children}
        </div>
        {footer && (
          <div className="flex items-center justify-end gap-3 border-t border-border-gray px-6 py-4 bg-secondary">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};
