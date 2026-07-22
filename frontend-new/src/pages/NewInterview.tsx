import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as zod from 'zod';
import { Upload, FileText, AlertCircle, Sparkles, Video, Volume2 } from 'lucide-react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { TextArea } from '../components/TextArea';
import { startInterview, parseResumeFile, uploadInterviewAudio } from '../api/interview';

const schema = zod.object({
  jd: zod.string().min(10, 'Job description must be at least 10 characters.'),
  resume: zod.string().optional(),
  custom_prompt: zod.string().optional(),
  meeting_url: zod.string().optional(),
});

type FormData = zod.infer<typeof schema>;

export const DEFAULT_INTERVIEWER_PROMPT = `You are Miaa, a professional, warm, and encouraging mock interviewer conducting a voice-based screening interview to help a candidate practice.

Interview flow:
1. Greet the candidate warmly, introducing yourself as Miaa, stating the specific role you're mock-interviewing them for (pulled from the Job Description).
2. Ask a total of 3 to 4 questions throughout the interview, one at a time. Mix technical and behavioral questions based on the candidate's resume and job description.
3. Ask only one question per turn, then stop and wait for their answer.
4. After each answer, give a brief natural acknowledgment before moving to the next question.
5. Closing: let the candidate know the mock interview is complete, give concise feedback, and say goodbye.

Voice output rules:
- Speak in short, natural sentences, 1 to 3 sentences per turn.
- Do not use emojis, bullet points, asterisks, headers, or markdown of any kind.
- Spell out all numbers (say "three" not "3").
- Avoid special characters.`;

export const DEFAULT_COPILOT_PROMPT = `You are an expert technical co-pilot. Your job is to assist the INTERVIEWER in real-time. You must NEVER speak to the candidate directly.

Real-Time Guidance Rules:
1. Evaluate candidate technical accuracy, confidence, and practical depth.
2. Recommend 2-3 follow-up questions tailored to missing concepts or partial answers.
3. Provide scenario-based architecture and coding questions for deep technical verification.
4. Generate verification questions to verify candidate resume claims.
5. Suggest the recommended next topic for the interviewer.`;

export const NewInterview: React.FC = () => {
  const navigate = useNavigate();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isParsing, setIsParsing] = useState(false);
  
  // Selection states: 'local' | 'teams' | 'simulation'
  const [interviewType, setInterviewType] = useState<'local' | 'teams' | 'simulation'>('local');
  
  // Simulation audio file state
  const [simulationAudioFile, setSimulationAudioFile] = useState<File | null>(null);
  
  // File upload state
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState<string>('');

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      jd: '',
      resume: '',
      custom_prompt: DEFAULT_INTERVIEWER_PROMPT,
    },
  });

  const handleSelectMode = (mode: 'local' | 'teams' | 'simulation') => {
    setInterviewType(mode);
    const targetPrompt = mode === 'local' ? DEFAULT_INTERVIEWER_PROMPT : DEFAULT_COPILOT_PROMPT;
    setValue('custom_prompt', targetPrompt);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadedFile(file);
    setErrorMsg(null);
    setIsParsing(true);

    // Convert file to base64 for session submission
    const reader = new FileReader();
    reader.onload = (event) => {
      const result = event.target?.result as string;
      const base64Data = result.split(',')[1] || result;
      setFileBase64(base64Data);
    };
    reader.readAsDataURL(file);

    try {
      // Call backend parser endpoint directly
      const extractedText = await parseResumeFile(file);
      if (!extractedText || extractedText.trim().length === 0) {
        setErrorMsg('We detected zero text in this file. Please manually paste or type your resume details in the text box below.');
      } else {
        setValue('resume', extractedText);
      }
    } catch (err: any) {
      console.error('Failed to parse resume file:', err);
      setErrorMsg(
        err.response?.data?.detail || 
        err.message || 
        'Failed to automatically parse the file. Please manually paste or type your resume details in the text box below.'
      );
    } finally {
      setIsParsing(false);
    }
  };

  const onSubmit = async (data: FormData) => {
    if (!uploadedFile) {
      setErrorMsg('Please upload a resume file (.pdf or .txt) before initializing the interview.');
      return;
    }
    if (!data.resume || data.resume.trim().length === 0) {
      setErrorMsg('Please wait for the resume text to be extracted or manually paste/type it in the text box below.');
      return;
    }
    if (interviewType === 'teams' && (!data.meeting_url || data.meeting_url.trim().length === 0)) {
      setErrorMsg('Please enter a valid Microsoft Teams meeting URL to start the Copilot Observer.');
      return;
    }
    if (interviewType === 'simulation' && !simulationAudioFile) {
      setErrorMsg('Please upload a recorded interview audio file (.wav) for simulation testing.');
      return;
    }
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const response = await startInterview({
        jd: data.jd,
        resume: data.resume || '',
        custom_prompt: data.custom_prompt || '',
        resume_filename: uploadedFile.name,
        resume_base64: fileBase64,
        meeting_url: interviewType === 'teams' ? data.meeting_url : '',
      });

      if (interviewType === 'simulation' && simulationAudioFile) {
        // Upload the audio file to the newly created session folder
        await uploadInterviewAudio(response.session_id, simulationAudioFile);
        // Redirect to Copilot room with simulate query parameter!
        navigate(`/copilots/${response.session_id}?simulate=true`);
      } else if (interviewType === 'teams') {
        // Redirect directly to Copilot Room since the bot is joining
        navigate(`/copilots/${response.session_id}`);
      } else {
        // Direct local voice bot interview
        navigate(`/interviews/${response.session_id}`);
      }
    } catch (err: any) {
      console.error(err);
      setErrorMsg(err.response?.data?.detail || err.message || 'Failed to start interview session.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-primary">Start a New Mock Interview</h1>
        <p className="text-sm text-muted-gray mt-1">
          Provide the target Job Description and Candidate Resume to initiate your session.
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
          {/* Interview Type Selector */}
          <div className="space-y-3">
            <span className="text-xs font-semibold text-primary uppercase tracking-wider">Select Operating Mode</span>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div 
                onClick={() => handleSelectMode('local')}
                className={`border p-4 rounded-xl cursor-pointer transition-all flex flex-col items-start gap-2.5 relative ${
                  interviewType === 'local' 
                    ? 'border-primary bg-primary/5 text-primary' 
                    : 'border-border-gray hover:border-primary/50 text-muted-gray bg-white'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Sparkles className="h-5 w-5 shrink-0" />
                  <span className="font-bold text-sm">Direct AI Voice Interview</span>
                </div>
                <p className="text-xs leading-relaxed">
                  The candidate speaks directly into the browser to the virtual AI interviewer.
                </p>
              </div>

              <div 
                onClick={() => handleSelectMode('teams')}
                className={`border p-4 rounded-xl cursor-pointer transition-all flex flex-col items-start gap-2.5 relative ${
                  interviewType === 'teams' 
                    ? 'border-primary bg-primary/5 text-primary' 
                    : 'border-border-gray hover:border-primary/50 text-muted-gray bg-white'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Video className="h-5 w-5 shrink-0 animate-pulse" />
                  <span className="font-bold text-sm">Teams Copilot Observer</span>
                </div>
                <p className="text-xs leading-relaxed">
                  AI acts as a silent observer in your Teams call, transcribing and guiding you on the dashboard.
                </p>
              </div>

              <div 
                onClick={() => handleSelectMode('simulation')}
                className={`border p-4 rounded-xl cursor-pointer transition-all flex flex-col items-start gap-2.5 relative ${
                  interviewType === 'simulation' 
                    ? 'border-primary bg-primary/5 text-primary' 
                    : 'border-border-gray hover:border-primary/50 text-muted-gray bg-white'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Volume2 className="h-5 w-5 shrink-0" />
                  <span className="font-bold text-sm">Local Audio Upload Test</span>
                </div>
                <p className="text-xs leading-relaxed">
                  Upload an interview recording (.wav file) to dry-run and test suggestions on the dashboard.
                </p>
              </div>
            </div>
          </div>

          {/* Conditional Teams URL Input */}
          {interviewType === 'teams' && (
            <div className="space-y-1.5 animate-fadeIn">
              <label htmlFor="meeting_url" className="text-xs font-semibold text-primary">
                Microsoft Teams Meeting Link
              </label>
              <input
                type="text"
                id="meeting_url"
                placeholder="https://teams.microsoft.com/l/meetup-join/..."
                className="flex h-10 w-full rounded-lg border border-border-gray bg-white px-3 py-2 text-sm text-primary placeholder:text-muted-gray focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                {...register('meeting_url')}
              />
              <p className="text-[10px] text-muted-gray">
                Provide the full Microsoft Teams invitation link. A silent observer bot will join to stream call audio.
              </p>
            </div>
          )}

          {/* Conditional Simulation Audio File Upload */}
          {interviewType === 'simulation' && (
            <div className="space-y-1.5 animate-fadeIn">
              <span className="text-xs font-semibold text-primary">Recorded Interview Audio File (.wav)</span>
              <div className="border border-dashed border-border-gray hover:border-primary/50 transition-colors rounded-lg p-5 bg-secondary flex flex-col items-center justify-center cursor-pointer relative group">
                <input
                  type="file"
                  accept=".wav"
                  className="absolute inset-0 opacity-0 cursor-pointer"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) setSimulationAudioFile(file);
                  }}
                  disabled={isSubmitting}
                />
                <Upload className="h-6 w-6 text-muted-gray group-hover:text-primary mb-1.5 transition-colors" />
                {simulationAudioFile ? (
                  <div className="flex items-center gap-2 text-sm text-primary font-medium">
                    <Volume2 className="h-4 w-4 text-primary" />
                    {simulationAudioFile.name} ({(simulationAudioFile.size / 1024 / 1024).toFixed(1)} MB)
                  </div>
                ) : (
                  <div className="text-center">
                    <p className="text-xs text-primary font-medium">Click or drag WAV file to upload for simulation</p>
                    <p className="text-[10px] text-muted-gray mt-0.5">Please upload mono 16kHz WAV files</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Job Description */}
          <TextArea
            label="Job Description (JD)"
            id="jd"
            placeholder="Paste the job description or role requirements here..."
            error={errors.jd?.message}
            {...register('jd')}
          />

          {/* Resume Upload Box */}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold text-primary">Resume File</span>
            <div className="border border-dashed border-border-gray hover:border-primary/50 transition-colors rounded-lg p-6 bg-secondary flex flex-col items-center justify-center cursor-pointer relative group">
              <input
                type="file"
                accept=".txt,.pdf"
                className="absolute inset-0 opacity-0 cursor-pointer"
                onChange={handleFileUpload}
                disabled={isParsing || isSubmitting}
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
                      {uploadedFile.name} ({(uploadedFile.size / 1024).toFixed(1)} KB)
                    </div>
                  ) : (
                    <div className="text-center">
                      <p className="text-sm text-primary font-medium">Click or drag PDF or TXT to upload</p>
                      <p className="text-xs text-muted-gray mt-1">Supports PDF, TXT up to 10MB</p>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Resume Text */}
          <TextArea
            label="Resume Text Content (Optional)"
            id="resume"
            placeholder="Paste the plain text of your resume here..."
            error={errors.resume?.message}
            {...register('resume')}
          />

          {/* Custom prompt instructions */}
          <TextArea
            label={
              interviewType === 'local'
                ? "Voice Interviewer System Prompt (Editable & Replaceable)"
                : "Copilot Observer System Prompt (Editable & Replaceable)"
            }
            id="custom_prompt"
            rows={10}
            placeholder={
              interviewType === 'local'
                ? "Edit, modify, or replace the system instructions for the AI Voice Interviewer..."
                : "Edit, modify, or replace the system instructions for the Copilot Observer Assistant..."
            }
            error={errors.custom_prompt?.message}
            {...register('custom_prompt')}
          />
        </Card>

        {/* Action Button */}
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
            <Sparkles className="h-4 w-4 mr-2" />
            {isSubmitting
              ? (interviewType === 'simulation' ? 'Optimizing & Uploading Audio (16kHz Mono)...' : 'Initializing Interview...')
              : 'Initialize Interview'}
          </Button>
        </div>
      </form>
    </div>
  );
};
