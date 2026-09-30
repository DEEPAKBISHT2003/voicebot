import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as zod from 'zod';
import { Video, AlertCircle, Sparkles, ChevronDown, ChevronUp } from 'lucide-react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { Input } from '../components/Input';
import { TextArea } from '../components/TextArea';
import { ResumeUpload } from '../components/ResumeUpload';
import { parseResumeFile } from '../api/interview';
import { startCopilot, joinCopilotMeeting } from '../api/copilot';

const schema = zod.object({
  meeting_url: zod
    .string()
    .min(5, 'Microsoft Teams meeting URL is required.')
    .refine(
      (url) => url.startsWith('http://') || url.startsWith('https://'),
      'Please enter a valid URL (starting with https://)'
    ),
  candidate_name: zod.string().optional(),
  interviewer: zod.string().optional(),
  jd: zod.string().min(10, 'Job description must be at least 10 characters.'),
  resume: zod.string().min(10, 'Resume text is required (upload a file or paste text below).'),
  custom_prompt: zod.string().optional(),
});

type FormData = zod.infer<typeof schema>;

export const DEFAULT_COPILOT_PROMPT = `You are an expert technical assistant.

Real-Time Guidance Rules:
1. Evaluate candidate technical accuracy, confidence, and practical depth.
2. Recommend 2 follow-up questions tailored to missing concepts or partial answers.
3. Provide 2 scenario-based architecture and coding questions for deep technical verification.
4. Generate 2 verification questions to verify candidate resume claims.
5. Suggest the recommended next topic for the interviewer.`;

export const MeetingObserver: React.FC = () => {
  const navigate = useNavigate();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isParsing, setIsParsing] = useState(false);
  const [showAdvancedPrompt, setShowAdvancedPrompt] = useState(false);

  // File upload state
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      meeting_url: '',
      candidate_name: '',
      interviewer: '',
      jd: '',
      resume: '',
      custom_prompt: DEFAULT_COPILOT_PROMPT,
    },
  });

  const handleFileUpload = async (file: File) => {
    setUploadedFile(file);
    setErrorMsg(null);
    setIsParsing(true);

    try {
      const extractedText = await parseResumeFile(file);
      if (!extractedText || extractedText.trim().length === 0) {
        setErrorMsg('Zero text detected in this file. Please manually paste or type candidate resume details below.');
      } else {
        setValue('resume', extractedText, { shouldValidate: true });
      }
    } catch (err: any) {
      console.error('Failed to parse resume file:', err);
      setErrorMsg(
        err.response?.data?.detail ||
        err.message ||
        'Failed to automatically parse the file. Please manually paste or type candidate resume details below.'
      );
    } finally {
      setIsParsing(false);
    }
  };

  const onSubmit = async (data: FormData) => {
    setIsSubmitting(true);
    setErrorMsg(null);

    try {
      // 1. Initialize Copilot session
      const copilotResponse = await startCopilot({
        jd: data.jd,
        resume: data.resume || '',
        custom_prompt: data.custom_prompt || DEFAULT_COPILOT_PROMPT,
        interviewer: data.interviewer?.trim() || undefined,
        candidate_name: data.candidate_name?.trim() || undefined,
      });

      // 2. Trigger the Teams observer bot to join the meeting
      await joinCopilotMeeting(
        copilotResponse.session_id,
        data.meeting_url.trim(),
        'observer',
        'Appz Meeting Observer'
      );

      // 3. Redirect directly to the Copilot dashboard for live monitoring
      navigate(`/copilots/${copilotResponse.session_id}`);
    } catch (err: any) {
      console.error('Failed to start Meeting Observer:', err);
      setErrorMsg(err.response?.data?.detail || err.message || 'Failed to start Meeting Observer session.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h1 className="text-xl font-bold text-primary flex items-center gap-2.5">
          <Video className="h-6 w-6 text-primary shrink-0" />
          Appz Meeting Observer
        </h1>
        <p className="text-sm text-muted-gray mt-1">
          Deploy a silent AI observer bot to your Microsoft Teams meeting to capture real-time captions, analyze candidate responses, and provide live interview guidance.
        </p>
      </div>

      {errorMsg && (
        <div className="p-4 rounded-lg bg-yellow-50 border border-yellow-200 text-sm text-yellow-800 flex items-start gap-2.5">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <div>{errorMsg}</div>
        </div>
      )}

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <Card className="space-y-6">
          {/* Microsoft Teams Meeting Link */}
          <div className="space-y-1.5">
            <Input
              label="Microsoft Teams Meeting Link"
              id="meeting_url"
              placeholder="https://teams.microsoft.com/l/meetup-join/..."
              error={errors.meeting_url?.message}
              {...register('meeting_url')}
            />
            <p className="text-[11px] text-muted-gray">
              Provide the full Microsoft Teams invitation link. A silent observer bot will join the call to stream audio for transcription.
            </p>
          </div>

          {/* Interviewer & Candidate Info */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-1">
              <Input
                label="Interviewer Name (Optional)"
                id="interviewer"
                placeholder="e.g. Deepak Bisht, Mahima Soni"
                error={errors.interviewer?.message}
                {...register('interviewer')}
              />
              <p className="text-[11px] text-muted-gray">
                Name of the person taking the interview (can also be auto-detected from live captions).
              </p>
            </div>
            <div className="space-y-1">
              <Input
                label="Candidate Name (Optional)"
                id="candidate_name"
                placeholder="e.g. John Doe"
                error={errors.candidate_name?.message}
                {...register('candidate_name')}
              />
              <p className="text-[11px] text-muted-gray">
                Candidate's name (auto-extracted from resume if left empty).
              </p>
            </div>
          </div>

          {/* Job Description */}
          <TextArea
            label="Job Description (JD)"
            id="jd"
            rows={6}
            placeholder="Paste the target job description or role requirements here..."
            error={errors.jd?.message}
            {...register('jd')}
          />

          {/* Resume Upload Box */}
          <ResumeUpload
            uploadedFile={uploadedFile}
            isParsing={isParsing}
            onFileSelect={handleFileUpload}
            disabled={isSubmitting}
          />

          {/* Parsed Resume Content */}
          <TextArea
            label="Candidate Resume Content"
            id="resume"
            rows={8}
            placeholder="Parsed resume content will appear here, or you can paste candidate resume text directly..."
            error={errors.resume?.message}
            {...register('resume')}
          />

          {/* Advanced / Optional System Prompt Accordion */}
          <div className="border-t border-border-gray pt-4">
            <button
              type="button"
              onClick={() => setShowAdvancedPrompt(!showAdvancedPrompt)}
              className="flex items-center justify-between w-full text-xs font-semibold text-primary hover:text-primary/80 transition-colors"
            >
              <span className="flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5 text-muted-gray" />
                Advanced Observer Guidance Prompt (Optional)
              </span>
              {showAdvancedPrompt ? (
                <ChevronUp className="h-4 w-4 text-muted-gray" />
              ) : (
                <ChevronDown className="h-4 w-4 text-muted-gray" />
              )}
            </button>

            {showAdvancedPrompt && (
              <div className="mt-3 space-y-2 animate-fadeIn">
                <TextArea
                  id="custom_prompt"
                  rows={8}
                  placeholder="Customize the real-time evaluation and recommendation prompt for the observer..."
                  error={errors.custom_prompt?.message}
                  {...register('custom_prompt')}
                />
                <p className="text-[11px] text-muted-gray">
                  Customize the criteria and instruction rules the AI uses to evaluate candidate answers and suggest follow-up questions.
                </p>
              </div>
            )}
          </div>
        </Card>

        {/* Action Buttons */}
        <div className="flex justify-end gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() => navigate('/')}
            disabled={isSubmitting || isParsing}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            isLoading={isSubmitting}
            disabled={isParsing}
          >
            <Video className="h-4 w-4 mr-2" />
            {isSubmitting ? 'Joining Meeting as Observer...' : 'Launch Meeting Observer'}
          </Button>
        </div>
      </form>
    </div>
  );
};
