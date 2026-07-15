import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles, HelpCircle, ArrowRight } from 'lucide-react';
import { startCopilot } from '../../api/copilot';

export const NewCopilot: React.FC = () => {
  const navigate = useNavigate();
  const [jd, setJd] = useState('');
  const [resume, setResume] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jd.trim() || !resume.trim()) {
      setError('Please provide both the Job Description and your Resume.');
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
              rows={8}
              className="w-full rounded-lg border border-border-gray bg-white p-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Paste the target job description here..."
              value={jd}
              onChange={(e) => setJd(e.target.value)}
            />
          </div>

          {/* Resume */}
          <div className="flex flex-col gap-2">
            <label htmlFor="resume" className="text-sm font-semibold text-primary">
              Your Resume (Text)
            </label>
            <textarea
              id="resume"
              rows={8}
              className="w-full rounded-lg border border-border-gray bg-white p-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Paste your resume text here..."
              value={resume}
              onChange={(e) => setResume(e.target.value)}
            />
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
            disabled={loading}
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
