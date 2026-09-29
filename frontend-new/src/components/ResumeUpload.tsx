import React, { useRef } from 'react';
import { Upload, FileText } from 'lucide-react';

export interface ResumeUploadProps {
  uploadedFile: File | null;
  isParsing: boolean;
  onFileSelect: (file: File) => void;
  disabled?: boolean;
  label?: string;
  helperText?: string;
}

/**
 * Reusable Resume Upload dropzone component with parse-state indicator and file metadata display.
 */
export const ResumeUpload: React.FC<ResumeUploadProps> = ({
  uploadedFile,
  isParsing,
  onFileSelect,
  disabled = false,
  label = 'Resume File',
  helperText = 'Supports PDF, TXT up to 10MB',
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      onFileSelect(file);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (disabled || isParsing) return;
    const file = e.dataTransfer.files?.[0];
    if (file) {
      onFileSelect(file);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  };

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-semibold text-primary">{label}</span>
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onClick={() => {
          if (!disabled && !isParsing) {
            fileInputRef.current?.click();
          }
        }}
        className={`border border-dashed border-border-gray hover:border-primary/50 transition-colors rounded-lg p-6 bg-secondary flex flex-col items-center justify-center cursor-pointer relative group ${
          disabled || isParsing ? 'opacity-70 cursor-not-allowed' : ''
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.pdf"
          className="hidden"
          onChange={handleFileChange}
          disabled={disabled || isParsing}
        />
        {isParsing ? (
          <div className="text-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-2" />
            <p className="text-sm text-primary font-medium">Parsing resume file...</p>
          </div>
        ) : (
          <>
            <Upload className="h-8 w-8 text-muted-gray group-hover:text-primary mb-2 transition-colors" />
            {uploadedFile ? (
              <div className="flex items-center gap-2 text-sm text-primary font-medium">
                <FileText className="h-4 w-4 text-primary" />
                <span>{uploadedFile.name}</span>
                <span className="text-xs text-muted-gray">({(uploadedFile.size / 1024).toFixed(1)} KB)</span>
              </div>
            ) : (
              <div className="text-center">
                <p className="text-sm text-primary font-medium">Click or drag PDF or TXT to upload</p>
                <p className="text-xs text-muted-gray mt-1">{helperText}</p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
