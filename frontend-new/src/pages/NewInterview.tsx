import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as zod from 'zod';
import { Upload, FileText, AlertCircle, Sparkles } from 'lucide-react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { TextArea } from '../components/TextArea';
import { startInterview } from '../api/interview';
import { extractTextFromPDF } from '../utils/pdf';

const schema = zod.object({
  jd: zod.string().min(10, 'Job description must be at least 10 characters.'),
  resume: zod.string().optional(),
  custom_prompt: zod.string().optional(),
});

type FormData = zod.infer<typeof schema>;

export const NewInterview: React.FC = () => {
  const navigate = useNavigate();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  
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
      custom_prompt: '',
    },
  });

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadedFile(file);
    setErrorMsg(null);

    // Convert file to base64
    const reader = new FileReader();
    reader.onload = (event) => {
      const result = event.target?.result as string;
      // Get only the base64 payload part (remove headers like "data:application/pdf;base64,")
      const base64Data = result.split(',')[1] || result;
      setFileBase64(base64Data);

      // If it's a text file, prefill the resume text area automatically
      if (file.type === 'text/plain' || file.name.endsWith('.txt')) {
        const textReader = new FileReader();
        textReader.onload = (tEvent) => {
          const text = tEvent.target?.result as string;
          setValue('resume', text);
        };
        textReader.readAsText(file);
      } else if (file.type === 'application/pdf') {
        const arrayBufferReader = new FileReader();
        arrayBufferReader.onload = async (abEvent) => {
          try {
            const arrayBuffer = abEvent.target?.result as ArrayBuffer;
            const extractedText = await extractTextFromPDF(arrayBuffer);
            if (!extractedText || extractedText.trim().length === 0) {
              setErrorMsg('We detected zero text in this PDF (it might be a scanned image). Please manually paste or type your resume details in the text box below.');
            } else {
              setValue('resume', extractedText);
            }
          } catch (err) {
            console.error('Failed to parse PDF text:', err);
            setErrorMsg('Failed to automatically extract PDF text. Please copy/paste the plain text manually below.');
          }
        };
        arrayBufferReader.readAsArrayBuffer(file);
      }
    };
    reader.readAsDataURL(file);
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
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const response = await startInterview({
        jd: data.jd,
        resume: data.resume || '',
        custom_prompt: data.custom_prompt || '',
        resume_filename: uploadedFile.name,
        resume_base64: fileBase64,
      });
      navigate(`/interviews/${response.session_id}`);
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
              />
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
            label="Custom Interview Instructions (Optional)"
            id="custom_prompt"
            placeholder="e.g. Focus strictly on system design, ask harder coding questions, or adopt a strict tone..."
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
            disabled={isSubmitting}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            isLoading={isSubmitting}
          >
            <Sparkles className="h-4 w-4 mr-2" />
            Initialize Interview
          </Button>
        </div>
      </form>
    </div>
  );
};
