import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Search,
  FileAudio,
  FileText,
  Calendar,
  Eye,
  FolderOpen,
  Briefcase,
  ExternalLink,
  Award
} from 'lucide-react';
import { listInterviews, getRecordingUrl, getResumeUrl } from '../api/interview';
import type { InterviewSession } from '../types';
import { useAuth } from '../context/AuthContext';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { Badge } from '../components/Badge';
import { Skeleton } from '../components/Loader';
import { EmptyState } from '../components/EmptyState';
import { Modal } from '../components/Modal';


/**
 * Words that should NEVER appear as part of a candidate's valid name.
 * Any line or candidate segment containing these words is disqualified.
 */
const FORBIDDEN_NAME_WORDS = new Set([
  // Headers & section titles
  'curriculum',
  'vitae',
  'curriculumvitae',
  'cv',
  'resume',
  'profile',
  'profilesummary',
  'summary',
  'objective',
  'careerobjective',
  'about',
  'aboutme',
  'personal',
  'details',
  'personaldetails',
  'information',
  'info',
  'overview',
  'declaration',
  'reference',
  'references',
  'experience',
  'workexperience',
  'employment',
  'history',
  'education',
  'skill',
  'skills',
  'project',
  'projects',
  'certification',
  'certifications',
  'award',
  'awards',
  'achievement',
  'achievements',
  'publication',
  'publications',
  'hobby',
  'hobbies',
  'interest',
  'interests',
  'language',
  'languages',
  'biodata',

  // Contact fields & labels
  'email',
  'phone',
  'mobile',
  'tel',
  'telephone',
  'contact',
  'contactdetails',
  'contactinfo',
  'address',
  'location',
  'city',
  'country',
  'state',
  'zip',
  'postal',
  'gender',
  'dob',
  'birth',
  'nationality',

  // Common job roles & title keywords
  'engineer',
  'engineering',
  'developer',
  'development',
  'architect',
  'architecture',
  'designer',
  'design',
  'scientist',
  'science',
  'manager',
  'management',
  'analyst',
  'analytics',
  'consultant',
  'consulting',
  'programmer',
  'programming',
  'specialist',
  'administrator',
  'administration',
  'intern',
  'internship',
  'officer',
  'director',
  'lead',
  'leader',
  'leadership',
  'senior',
  'junior',
  'principal',
  'staff',
  'associate',
  'coder',
  'tester',
  'qa',
  'software',
  'hardware',
  'frontend',
  'backend',
  'fullstack',
  'stack',
  'product',
  'data',
  'devops',
  'cloud',
  'web',
  'marketing',
  'digitalmarketing',
  'seo',

  // Sentence words / verbs / resume filler
  'passionate',
  'seeking',
  'experienced',
  'motivated',
  'responsible',
  'dedicated',
  'driven',
  'enthusiastic',
  'proficient',
  'skilled',
  'years',
  'looking',
  'working',
  'worked',
  'specialized',
  'graduated',
  'student',
  'present',
  'current',
  'remote',
  'hybrid',
  'onsite',
  'page',
  'confidential',
  'with',
  'and',
  'for',
]);

// Matches URLs, social media links, domains, or protocols
const URL_OR_SOCIAL_REGEX = /(https?:\/\/|www\.|linkedin\.com|github\.com|behance|medium\.com|gitlab\.com|portfolio|\.github\.io|\.com|\.org|\.net|\.io|\.dev|\.ai)/i;

// Matches bullet points or list markers
const BULLET_REGEX = /[•*·#~✓✔]/;

// Regex patterns to immediately disqualify lines or candidate segments that match resume sections or job titles
const RESUME_HEADER_REGEX = /\b(curriculum|vitae|resume|biodata|bio-data|profile|summary|objective|overview|declaration|experience|education|skills|projects|certifications?|references?)\b/i;
const JOB_TITLE_REGEX = /\b(engineer|developer|architect|designer|scientist|manager|analyst|consultant|programmer|specialist|administrator|intern|coder|tester|director|lead|officer)\b/i;
const CONTACT_LABEL_REGEX = /\b(email|phone|mobile|tel|telephone|contact|address|location|linkedin|github)\b/i;

/**
 * Validates whether a candidate string looks like a person's name.
 *
 * Rules:
 * - Length between 2 and 35 characters.
 * - Allowed characters: alphabetic letters (including accented characters), spaces, apostrophes, hyphens, and periods.
 * - Must contain at least 2 alphabetic characters.
 * - Contains 1 to 4 words.
 * - Does not contain excessive or malformed punctuation (e.g. consecutive dashes, trailing hyphens).
 * - None of the constituent words match forbidden resume metadata, job titles, or contact labels.
 */
const isValidCandidateName = (candidate: string): boolean => {
  if (!candidate) return false;
  const trimmed = candidate.trim();

  // Rule 4: Must be between 2 and 35 characters
  if (trimmed.length < 2 || trimmed.length > 35) return false;

  // Rule 4: Allowed characters: letters (including accented/latin extended), spaces, apostrophes, hyphens, periods
  if (!/^[a-zA-ZÀ-ÿ\s.'-]+$/.test(trimmed)) return false;

  // Must contain at least 2 alphabetic characters
  const lettersOnly = trimmed.replace(/[^a-zA-ZÀ-ÿ]/g, '');
  if (lettersOnly.length < 2) return false;

  // Rule 3 & 4: Contain 1-4 words (split by whitespace)
  const words = trimmed.split(/\s+/).filter(Boolean);
  if (words.length < 1 || words.length > 4) return false;

  // No excessive or malformed punctuation
  if (/[-.']{2,}/.test(trimmed)) return false;
  if (/^[-']|[-']$/.test(trimmed)) return false;

  // Disqualify if the candidate string matches header, job title, or contact labels
  if (RESUME_HEADER_REGEX.test(trimmed)) return false;
  if (JOB_TITLE_REGEX.test(trimmed)) return false;
  if (CONTACT_LABEL_REGEX.test(trimmed)) return false;

  // Check sub-words (splitting on whitespace, hyphens, underscores, dots, or slashes)
  // This catches hyphenated headers like "CURRICULUM-VITAE", "BIO-DATA", "ABOUT-ME", etc.
  const subWords = trimmed.split(/[\s\-_/.]+/).filter(Boolean);
  for (const word of subWords) {
    const cleanWord = word.toLowerCase().replace(/[^a-z]/g, '');
    if (!cleanWord) continue;
    if (FORBIDDEN_NAME_WORDS.has(cleanWord)) {
      return false;
    }
  }

  return true;
};

/**
 * Enhanced heuristic-based candidate name extraction mechanism.
 *
 * Replaces simple first-line assumption with a robust scanner:
 * - Scans the first 20 non-empty trimmed lines.
 * - Skips resume metadata, headers, contact info, URLs, phone numbers, and job titles.
 * - Supports composite lines with delimiters like '|', '•', or ' - ' (e.g., "John Doe | Senior Engineer").
 * - Returns "Unknown Candidate" if no confident match is found.
 */
const extractCandidateName = (resumeText: string): string => {
  if (!resumeText || typeof resumeText !== 'string') {
    return 'Unknown Candidate';
  }

  // Rule 1: Parse resume text into non-empty trimmed lines
  const lines = resumeText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);

  if (lines.length === 0) {
    return 'Unknown Candidate';
  }

  // Rule 1: Inspect the first 15–20 non-empty lines
  const candidateLines = lines.slice(0, 20);

  for (const rawLine of candidateLines) {
    // Rule 3: Reject lines with bullet points
    if (BULLET_REGEX.test(rawLine)) {
      continue;
    }

    // Rule 2: Reject lines containing email addresses
    if (/@/.test(rawLine)) {
      continue;
    }

    // Rule 2: Reject lines containing URLs or social links
    if (URL_OR_SOCIAL_REGEX.test(rawLine)) {
      continue;
    }

    // Rule 2: Reject lines starting with or containing phone numbers / excessive digits
    if (/^(\+|\d{1,4}[\s-]?\(?\d{2,4}\)?)/.test(rawLine) || (rawLine.match(/\d/g) || []).length >= 4) {
      continue;
    }

    // Remove any trailing parenthetical info e.g. "John Doe (He/Him)"
    const cleanedLine = rawLine.replace(/\s*\([^)]*\)/g, '').trim();
    if (!cleanedLine) continue;

    // Check if the whole cleaned line is a valid candidate name
    if (isValidCandidateName(cleanedLine)) {
      return cleanedLine;
    }

    // Check delimited segments e.g. "John Doe | Software Engineer" or "Curriculum Vitae | John Doe"
    if (/[|,]|\s+-\s+/.test(cleanedLine)) {
      const segments = cleanedLine
        .split(/[|,]|\s+-\s+/)
        .map((s) => s.trim())
        .filter((s) => s.length > 0);

      for (const segment of segments) {
        if (isValidCandidateName(segment)) {
          return segment;
        }
      }
    }
  }

  // Rule 5: Confidence-Based Fallback
  return 'Unknown Candidate';
};

export const getInterviewerDisplayName = (session: InterviewSession): { name: string; isDetected: boolean } => {
  const ignoredNames = new Set([
    'appz meeting observer', 'mia', 'mia (ai)', 'appz interviewer',
    'observer', 'system', 'candidate', 'speaker 3', '0', '1', 'none', 'null'
  ]);

  if (session.interviewer && session.interviewer.trim()) {
    const trimmed = session.interviewer.trim();
    if (!ignoredNames.has(trimmed.toLowerCase()) && !/^\d+$/.test(trimmed)) {
      return { name: trimmed, isDetected: true };
    }
  }

  // Backup extraction from transcript if not already populated
  if (Array.isArray(session.transcript) && session.transcript.length > 0) {
    const candName = (session.candidate_name || extractCandidateName(session.resume)).toLowerCase();

    // 1. Look for explicit interviewer / assistant roles
    for (const item of session.transcript) {
      const spk = (item.speaker || item.speaker_name || '').trim();
      const role = (item.speaker_role || item.role || '').toLowerCase();
      if (
        spk &&
        !ignoredNames.has(spk.toLowerCase()) &&
        !/^\d+$/.test(spk) &&
        !spk.toLowerCase().startsWith('speaker') &&
        spk.toLowerCase() !== candName
      ) {
        if (role === 'interviewer' || role === 'assistant') {
          return { name: spk, isDetected: true };
        }
      }
    }

    // 2. Look for non-candidate human speakers
    for (const item of session.transcript) {
      const spk = (item.speaker || item.speaker_name || '').trim();
      const role = (item.speaker_role || item.role || '').toLowerCase();
      if (
        spk &&
        !ignoredNames.has(spk.toLowerCase()) &&
        !/^\d+$/.test(spk) &&
        !spk.toLowerCase().startsWith('speaker') &&
        spk.toLowerCase() !== candName &&
        role !== 'candidate'
      ) {
        return { name: spk, isDetected: true };
      }
    }
  }

  return { name: 'Not detected yet', isDetected: false };
};

export const InterviewsList: React.FC = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'ADMIN';
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSession, setSelectedSession] = useState<InterviewSession | null>(null);

  // Load interviews using TanStack Query
  const { data: sessions = [], isLoading, error } = useQuery<InterviewSession[]>({
    queryKey: ['interviews'],
    queryFn: listInterviews,
    refetchInterval: 30000, // refresh every 30s to check for updates
    staleTime: 10000, // consider fresh for 10s
  });

  const formatDate = (isoString: string | null): string => {
    if (!isoString) return 'Date unknown';
    try {
      const date = new Date(isoString);
      return date.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  // Filter records
  const filteredSessions = sessions.filter((session) => {
    const candidate = (session.candidate_name || extractCandidateName(session.resume)).toLowerCase();
    const id = session.session_id.toLowerCase();
    const interviewerInfo = getInterviewerDisplayName(session);
    const interviewer = interviewerInfo.isDetected ? interviewerInfo.name.toLowerCase() : '';
    const organizer = (session.organizer_email || session.organizer || '').toLowerCase();
    const query = searchQuery.toLowerCase();
    return candidate.includes(query) || id.includes(query) || interviewer.includes(query) || organizer.includes(query);
  });

  return (
    <div className="space-y-6">
      {/* Header section */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-bold text-primary">Interviews & Meetings Directory</h1>
            {isAdmin ? (
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800 border border-purple-200">
                Admin View (All Meetings)
              </span>
            ) : (
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-200">
                My Organized Meetings
              </span>
            )}
          </div>
          <p className="text-sm text-muted-gray mt-1">
            {isAdmin
              ? 'Complete company directory of meetings observed by Appz Meeting Observer across all organizers.'
              : 'Access your organized meeting observer logs, audio recordings, and evaluation dossiers.'}
          </p>
        </div>
      </div>

      {/* Filter and search bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-gray" />
          <input
            type="text"
            placeholder="Search by candidate, session ID, interviewer, or organizer..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex h-10 w-full rounded-lg border border-border-gray bg-white pl-9 pr-3 py-2 text-sm text-primary placeholder:text-muted-gray focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          />
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((n) => (
            <Card key={n} className="flex flex-col gap-3">
              <Skeleton className="h-4 w-1/4" />
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-8 w-full" />
            </Card>
          ))}
        </div>
      ) : error ? (
        <div className="p-6 text-center border border-red-200 bg-red-50 rounded-lg text-sm text-red-700">
          Failed to fetch session records. Please verify the backend is running.
        </div>
      ) : filteredSessions.length === 0 ? (
        <EmptyState
          title={searchQuery ? 'No matching records' : 'No meeting sessions'}
          description={
            searchQuery
              ? `We couldn't find any session matching "${searchQuery}". Try adjusting your keywords.`
              : isAdmin
              ? 'No meeting observer sessions have been recorded yet.'
              : 'You haven\'t launched any meeting observer sessions yet. Launch one to see it listed here.'
          }
          actionLabel={searchQuery ? undefined : 'Launch Meeting Observer'}
          onAction={searchQuery ? undefined : () => navigate('/meeting-observer')}
          icon={<FolderOpen className="h-6 w-6" />}
        />
      ) : (
        <div className="border border-border-gray rounded-lg overflow-x-auto bg-white">
          <table className="w-full text-left border-collapse table-fixed">
            <thead>
              <tr className="border-b border-border-gray bg-secondary text-xs font-semibold text-primary">
                <th className="px-5 py-4 w-[18%] whitespace-nowrap">Candidate Name</th>
                <th className="px-5 py-4 w-[12%] whitespace-nowrap">Session ID</th>
                <th className="px-5 py-4 w-[17%] whitespace-nowrap">Interviewer</th>
                <th className="px-5 py-4 w-[17%] whitespace-nowrap">Organizer</th>
                <th className="px-5 py-4 w-[16%] whitespace-nowrap">Date & Time</th>
                <th className="px-5 py-4 w-[10%] whitespace-nowrap">Status</th>
                <th className="px-5 py-4 w-[10%] text-right whitespace-nowrap">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-gray text-sm">
              {filteredSessions.map((session) => {
                const interviewerInfo = getInterviewerDisplayName(session);
                return (
                  <tr key={session.session_id} className="hover:bg-secondary/40 transition-colors">
                    <td className="px-5 py-4 font-medium text-primary truncate" title={session.candidate_name || extractCandidateName(session.resume)}>
                      {session.candidate_name || extractCandidateName(session.resume)}
                    </td>
                    <td className="px-5 py-4 font-mono text-xs text-muted-gray whitespace-nowrap">
                      {session.session_id.substring(0, 8)}...
                    </td>
                    <td className="px-5 py-4 text-xs text-primary whitespace-nowrap">
                      {interviewerInfo.isDetected ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-800 border border-slate-200">
                          {interviewerInfo.name}
                        </span>
                      ) : (
                        <span className="text-muted-gray text-xs italic">
                          {session.transcript && session.transcript.length > 0 ? "—" : "Not detected yet"}
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-4 text-xs text-muted-gray whitespace-nowrap truncate" title={session.organizer_email || session.organizer || 'Unknown'}>
                      <div className="flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-500 shrink-0" />
                        <span className="truncate">{session.organizer_email || session.organizer || 'Unknown'}</span>
                      </div>
                    </td>
                    <td className="px-5 py-4 text-muted-gray text-xs whitespace-nowrap">
                      <div className="inline-flex items-center gap-1.5">
                        <Calendar className="h-3.5 w-3.5" />
                        <span>{formatDate(session.timestamp)}</span>
                      </div>
                    </td>
                    <td className="px-5 py-4 whitespace-nowrap">
                      {session.transcript.length > 0 ? (
                        <Badge variant="success">Completed</Badge>
                      ) : (
                        <Badge variant="warning">No Transcript</Badge>
                      )}
                    </td>
                    <td className="px-5 py-4 text-right whitespace-nowrap">
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setSelectedSession(session)}
                        >
                          <Eye className="h-3.5 w-3.5 mr-1" />
                          View
                        </Button>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => navigate(`/copilots/${session.session_id}`)}
                        >
                          <FileText className="h-3.5 w-3.5 mr-1" />
                          Results
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Transcript Detail Modal */}
      {selectedSession && (
        <Modal
          isOpen={!!selectedSession}
          onClose={() => setSelectedSession(null)}
          title={`Interview Record - ${extractCandidateName(selectedSession.resume)}`}
          size="lg"
          footer={
            <div className="flex w-full justify-between items-center">
              <span className="text-xs text-muted-gray font-mono">
                ID: {selectedSession.session_id}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => navigate(`/copilots/${selectedSession.session_id}`)}
                >
                  <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
                  Open Full Dossier
                </Button>
                <a
                  href={getResumeUrl(selectedSession.session_id)}
                  download
                  className="inline-flex items-center justify-center font-medium rounded-lg transition-colors border border-border-gray bg-white text-primary hover:bg-secondary px-3 py-1.5 text-xs"
                >
                  <FileText className="h-3.5 w-3.5 mr-1.5" />
                  Download Resume
                </a>
                <a
                  href={getRecordingUrl(selectedSession.session_id)}
                  download
                  className="inline-flex items-center justify-center font-medium rounded-lg transition-colors border border-border-gray bg-white text-primary hover:bg-secondary px-3 py-1.5 text-xs"
                >
                  <FileAudio className="h-3.5 w-3.5 mr-1.5" />
                  Download Audio
                </a>
              </div>
            </div>
          }
        >
          <div className="space-y-6">
            {/* Metadata segment */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 border-b border-border-gray pb-4 text-sm">
              <div>
                <span className="block font-semibold text-primary">Candidate Name</span>
                <span className="text-muted-gray">{selectedSession.candidate_name || extractCandidateName(selectedSession.resume)}</span>
              </div>
              <div>
                <span className="block font-semibold text-primary">Interviewer</span>
                <span className="text-muted-gray">
                  {selectedSession && getInterviewerDisplayName(selectedSession).isDetected
                    ? getInterviewerDisplayName(selectedSession).name
                    : "—"}
                </span>
              </div>
              <div>
                <span className="block font-semibold text-primary">Organizer</span>
                <span className="text-muted-gray truncate block" title={selectedSession.organizer_email || selectedSession.organizer || 'Unknown'}>
                  {selectedSession.organizer_email || selectedSession.organizer || 'Unknown'}
                </span>
              </div>
              <div>
                <span className="block font-semibold text-primary">Conducted on</span>
                <span className="text-muted-gray">{formatDate(selectedSession.timestamp)}</span>
              </div>
            </div>

            {/* Audio Playback & Resume View */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 border-b border-border-gray pb-4">
              {/* Audio Player */}
              <div>
                <span className="block font-semibold text-primary text-sm mb-2">
                  <FileAudio className="inline h-3.5 w-3.5 mr-1.5 -mt-0.5" />
                  Interview Recording
                </span>
                <audio
                  controls
                  preload="none"
                  src={getRecordingUrl(selectedSession.session_id)}
                  className="w-full h-10 rounded-lg"
                >
                  Your browser does not support the audio element.
                </audio>
              </div>

              {/* View Resume */}
              <div>
                <span className="block font-semibold text-primary text-sm mb-2">
                  <FileText className="inline h-3.5 w-3.5 mr-1.5 -mt-0.5" />
                  Candidate Resume
                </span>
                <a
                  href={getResumeUrl(selectedSession.session_id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center justify-center font-medium rounded-lg transition-colors border border-border-gray bg-white text-primary hover:bg-secondary px-4 py-2 text-sm w-full"
                >
                  <Eye className="h-3.5 w-3.5 mr-1.5" />
                  View Resume
                </a>
              </div>
            </div>

            {/* Job Description (JD) */}
            <div className="border-b border-border-gray pb-4">
              <span className="block font-semibold text-primary text-sm mb-2">
                <Briefcase className="inline h-3.5 w-3.5 mr-1.5 -mt-0.5" />
                Job Description (JD)
              </span>
              {selectedSession.jd ? (
                <div className="p-3 rounded-lg text-xs bg-secondary/50 border border-border-gray text-primary max-h-36 overflow-y-auto whitespace-pre-wrap font-mono leading-relaxed">
                  {selectedSession.jd}
                </div>
              ) : (
                <div className="text-xs text-muted-gray italic bg-secondary p-3 rounded-lg border border-border-gray">
                  No Job Description recorded for this session.
                </div>
              )}
            </div>

            {/* Final Report Preview / Dossier Link */}
            <div className="border-b border-border-gray pb-4">
              <span className="block font-semibold text-primary text-sm mb-2">
                <Award className="inline h-3.5 w-3.5 mr-1.5 -mt-0.5 text-primary" />
                Final Evaluation Report
              </span>
              {selectedSession.final_report ? (
                <div className="p-4 rounded-lg bg-secondary/60 border border-border-gray text-xs space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-primary uppercase text-[11px] tracking-wider">Persisted Dossier Available</span>
                    <button
                      onClick={() => navigate(`/copilots/${selectedSession.session_id}`)}
                      className="inline-flex items-center gap-1 font-bold text-primary hover:underline"
                    >
                      View Full Dossier <ExternalLink className="h-3 w-3" />
                    </button>
                  </div>
                  {selectedSession.final_report.intelligence?.covered_skills?.length > 0 && (
                    <div>
                      <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">Covered Skills:</span>
                      <div className="flex flex-wrap gap-1">
                        {selectedSession.final_report.intelligence.covered_skills.map((s: string, i: number) => (
                          <span key={i} className="px-2 py-0.5 bg-green-50 text-green-700 border border-green-200 rounded text-[10px] font-semibold">
                            {s}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {selectedSession.final_report.assistance?.interview_notes?.length > 0 && (
                    <div>
                      <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">Key Observer Notes:</span>
                      <p className="text-muted-gray italic leading-relaxed line-clamp-2">
                        {selectedSession.final_report.assistance.interview_notes[0]}
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="p-3 rounded-lg bg-secondary/40 border border-border-gray text-xs text-muted-gray flex items-center justify-between">
                  <span>Report not finalized yet for this session.</span>
                  <button
                    onClick={() => navigate(`/copilots/${selectedSession.session_id}`)}
                    className="font-bold text-primary hover:underline flex items-center gap-1 text-xs"
                  >
                    Open Session <ExternalLink className="h-3 w-3" />
                  </button>
                </div>
              )}
            </div>

            {/* Transcript list */}
            <div>
              <h4 className="font-semibold text-primary mb-3">Interview Transcript</h4>
              {selectedSession.transcript.length === 0 ? (
                <div className="text-sm text-muted-gray italic bg-secondary p-4 rounded-lg">
                  No conversation logs recorded for this session.
                </div>
              ) : (
                <div className="space-y-3.5 max-h-[40vh] overflow-y-auto pr-2">
                  {selectedSession.transcript.map((entry, index) => {
                    const isAI = entry.role === 'assistant' || entry.speaker === 'Interviewer';
                    const speakerLabel = isAI
                      ? '🤖 Appz Interviewer'
                      : entry.speaker
                      ? `🗣️ ${entry.speaker}`
                      : '🗣️ Candidate';
                    return (
                      <div
                        key={index}
                        className={`p-3 rounded-lg text-sm border ${
                          isAI
                            ? 'bg-secondary border-border-gray'
                            : 'bg-primary/5 border-primary/10'
                        }`}
                      >
                        <span className="block font-semibold text-xs text-primary mb-1">
                          {speakerLabel}
                        </span>
                        <p className="text-primary leading-relaxed">{entry.text}</p>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
