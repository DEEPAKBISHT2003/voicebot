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
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { Badge } from '../components/Badge';
import { Skeleton } from '../components/Loader';
import { EmptyState } from '../components/EmptyState';
import { Modal } from '../components/Modal';

export const InterviewsList: React.FC = () => {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSession, setSelectedSession] = useState<InterviewSession | null>(null);

  // Load interviews using TanStack Query
  const { data: sessions = [], isLoading, error } = useQuery<InterviewSession[]>({
    queryKey: ['interviews'],
    queryFn: listInterviews,
    refetchInterval: 30000, // refresh every 30s to check for updates
    staleTime: 10000, // consider fresh for 10s
  });

  const extractCandidateName = (resumeText: string): string => {
    if (!resumeText) return 'Unknown Candidate';
    const lines = resumeText.split('\n').map((l) => l.trim()).filter((l) => l.length > 0);
    if (lines.length === 0) return 'Unknown Candidate';
    const firstLine = lines[0];
    const name = firstLine.split(',')[0].split('|')[0].split('+')[0].split(' - ')[0].trim();
    const words = name.split(/\s+/);
    if (words.length > 4) {
      return words.slice(0, 3).join(' ');
    }
    if (!name || /^\d+$/.test(name)) {
      return 'Candidate';
    }
    return name;
  };

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
    const candidate = extractCandidateName(session.resume).toLowerCase();
    const id = session.session_id.toLowerCase();
    const query = searchQuery.toLowerCase();
    return candidate.includes(query) || id.includes(query);
  });

  return (
    <div className="space-y-6">
      {/* Header section */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-primary">Interviews Directory</h1>
          <p className="text-sm text-muted-gray mt-1">
            Access previous mock session logs, audio recordings, and transcripts.
          </p>
        </div>
      </div>

      {/* Filter and search bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-gray" />
          <input
            type="text"
            placeholder="Search by candidate name or session ID..."
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
          title={searchQuery ? 'No matching records' : 'No interview sessions'}
          description={
            searchQuery
              ? `We couldn't find any session matching "${searchQuery}". Try adjusting your keywords.`
              : 'You haven\'t conducted any mock interviews yet. Start one to see it listed here.'
          }
          actionLabel={searchQuery ? undefined : 'Start Mock Session'}
          onAction={searchQuery ? undefined : () => navigate('/interviews/new')}
          icon={<FolderOpen className="h-6 w-6" />}
        />
      ) : (
        <div className="border border-border-gray rounded-lg overflow-x-auto bg-white">
          <table className="w-full text-left border-collapse table-fixed">
            <thead>
              <tr className="border-b border-border-gray bg-secondary text-xs font-semibold text-primary">
                <th className="px-6 py-4 w-[26%] whitespace-nowrap">Candidate Name</th>
                <th className="px-6 py-4 w-[18%] whitespace-nowrap">Session ID</th>
                <th className="px-6 py-4 w-[24%] whitespace-nowrap">Date & Time</th>
                <th className="px-6 py-4 w-[14%] whitespace-nowrap">Status</th>
                <th className="px-6 py-4 w-[18%] text-right whitespace-nowrap">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-gray text-sm">
              {filteredSessions.map((session) => {
                return (
                  <tr key={session.session_id} className="hover:bg-secondary/40 transition-colors">
                    <td className="px-6 py-4 font-medium text-primary truncate">
                      {extractCandidateName(session.resume)}
                    </td>
                    <td className="px-6 py-4 font-mono text-xs text-muted-gray whitespace-nowrap">
                      {session.session_id.substring(0, 8)}...
                    </td>
                    <td className="px-6 py-4 text-muted-gray whitespace-nowrap">
                      <div className="inline-flex items-center gap-1.5">
                        <Calendar className="h-3.5 w-3.5" />
                        <span>{formatDate(session.timestamp)}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {session.transcript.length > 0 ? (
                        <Badge variant="success">Completed</Badge>
                      ) : (
                        <Badge variant="warning">No Transcript</Badge>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right whitespace-nowrap">
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
            <div className="grid grid-cols-2 gap-4 border-b border-border-gray pb-4 text-sm">
              <div>
                <span className="block font-semibold text-primary">Candidate Name</span>
                <span className="text-muted-gray">{extractCandidateName(selectedSession.resume)}</span>
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
