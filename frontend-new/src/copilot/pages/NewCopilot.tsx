import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles, HelpCircle, ArrowRight, Upload, FileText } from 'lucide-react';
import { startCopilot } from '../../api/copilot';
import { parseResumeFile } from '../../api/interview';

export const NewCopilot: React.FC = () => {
  const navigate = useNavigate();
  const [jd, setJd] = useState('');
  const [resume, setResume] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  
  // File upload state
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [isParsing, setIsParsing] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadedFile(file);
    setError(null);
    setIsParsing(true);

    try {
      // Call backend parser endpoint directly
      const extractedText = await parseResumeFile(file);
      if (!extractedText || extractedText.trim().length === 0) {
        setError('We detected zero text in this file. Please manually paste or type your resume details in the text box below.');
      } else {
        setResume(extractedText);
      }
    } catch (err: any) {
      console.error('Failed to parse resume file:', err);
      setError(
        err.response?.data?.detail || 
        err.message || 
        'Failed to automatically parse the file. Please manually paste or type your resume details in the text box below.'
      );
    } finally {
      setIsParsing(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jd.trim() || !resume.trim()) {
      setError('Please provide both the Job Description and your Resume (by upload or text).');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await startCopilot({
        jd,
        resume,
        custom_prompt: customPrompt,
      });
      navigate(`/copilots/${response.session_id}`);
    } catch (err: any) {
      console.error(err);
      setError(err.message || 'Failed to start AI Copilot session.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1.5">
        <h1 className="text-2xl font-bold tracking-tight text-primary flex items-center gap-2">
          <Sparkles className="h-6 w-6 text-primary" />
          AI Copilot Mode
        </h1>
        <p className="text-sm text-muted-gray">
          Configure your copilot to assist you during your preparation.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="bg-secondary rounded-xl p-6 border border-border-gray space-y-5">
        {error && (
          <div className="bg-red-50 text-red-600 text-sm p-4 rounded-lg border border-red-100">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Job Description */}
          <div className="flex flex-col gap-2">
            <label htmlFor="jd" className="text-sm font-semibold text-primary">
              Target Job Description
            </label>
            <textarea
              id="jd"
              rows={15}
              className="w-full rounded-lg border border-border-gray bg-white p-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Paste the target job description here..."
              value={jd}
              onChange={(e) => setJd(e.target.value)}
            />
          </div>

          {/* Resume Upload & Text */}
          <div className="flex flex-col gap-4">
            {/* Resume Upload Box */}
            <div className="flex flex-col gap-2">
              <span className="text-sm font-semibold text-primary">Resume File</span>
              <div className="border border-dashed border-border-gray hover:border-primary/50 transition-colors rounded-lg p-5 bg-white flex flex-col items-center justify-center cursor-pointer relative group">
                <input
                  type="file"
                  accept=".txt,.pdf"
                  className="absolute inset-0 opacity-0 cursor-pointer"
                  onChange={handleFileUpload}
                  disabled={isParsing || loading}
                />
                {isParsing ? (
                  <div className="text-center py-2">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-2" />
                    <p className="text-xs text-primary font-medium">Parsing resume file...</p>
                  </div>
                ) : (
                  <>
                    <Upload className="h-6 w-6 text-muted-gray group-hover:text-primary mb-2 transition-colors" />
                    {uploadedFile ? (
                      <div className="flex items-center gap-2 text-xs text-primary font-bold">
                        <FileText className="h-4 w-4 text-primary animate-pulse" />
                        {uploadedFile.name} ({(uploadedFile.size / 1024).toFixed(1)} KB)
                      </div>
                    ) : (
                      <div className="text-center">
                        <p className="text-xs text-primary font-bold">Click or drag PDF or TXT to upload</p>
                        <p className="text-[10px] text-muted-gray mt-1">Supports PDF, TXT up to 10MB</p>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>

            {/* Resume Text */}
            <div className="flex flex-col gap-2">
              <label htmlFor="resume" className="text-sm font-semibold text-primary">
                Your Resume (Text - Optional if uploaded)
              </label>
              <textarea
                id="resume"
                rows={6}
                className="w-full rounded-lg border border-border-gray bg-white p-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                placeholder="Parsed resume text will appear here automatically, or you can paste it manually..."
                value={resume}
                onChange={(e) => setResume(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Custom Instructions */}
        <div className="flex flex-col gap-2">
          <label htmlFor="customPrompt" className="text-sm font-semibold text-primary flex items-center gap-1.5">
            Custom Instructions
            <HelpCircle className="h-4 w-4 text-muted-gray" />
          </label>
          <input
            id="customPrompt"
            type="text"
            className="w-full rounded-lg border border-border-gray bg-white p-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            placeholder="E.g., Focus on system design, emphasize behavioral details..."
            value={customPrompt}
            onChange={(e) => setCustomPrompt(e.target.value)}
          />
        </div>

        {/* Submit */}
        <div className="flex justify-end pt-2">
          <button
            type="submit"
            disabled={loading || isParsing}
            className="flex items-center gap-2 px-5 py-2.5 bg-primary text-white text-sm font-medium rounded-lg hover:bg-primary/95 disabled:bg-primary/50 transition-colors"
          >
            {loading ? 'Initializing...' : 'Launch Copilot'}
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </form>
    </div>
  );
};
