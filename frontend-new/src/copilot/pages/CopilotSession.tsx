import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { 
  Mic, 
  MessageSquare, 
  Power, 
  ArrowLeft, 
  User, 
  Activity, 
  BookOpen, 
  Briefcase, 
  CheckCircle, 
  ChevronDown, 
  ChevronUp, 
  Compass
} from 'lucide-react';
import { useCopilotAudio } from '../hooks/useCopilotAudio';
import { stopCopilot } from '../../api/copilot';

export const CopilotSession: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  
  const { 
    status, 
    error, 
    transcript, 
    intelligence, 
    assistance, 
    startConnection, 
    stopConnection
  } = useCopilotAudio(id || null);

  const [expandedMessageIndex, setExpandedMessageIndex] = useState<number | null>(null);

  const handleEndSession = async () => {
    stopConnection();
    if (id) {
      try {
        await stopCopilot(id);
      } catch (e) {
        console.error('Failed to stop copilot session on backend:', e);
      }
    }
    navigate('/');
  };

  const toggleMessageExpand = (index: number) => {
    setExpandedMessageIndex((prev) => (prev === index ? null : index));
  };

  // Helper to calculate candidate average score from latest evaluations
  const getCandidateScore = () => {
    const candidateMessages = transcript.filter(m => m.speaker === 'Candidate' && m.evaluation);
    if (candidateMessages.length === 0) return 'N/A';
    
    let totalAccuracy = 0;
    let count = 0;
    candidateMessages.forEach(m => {
      const accuracy = m.evaluation?.technical_accuracy?.rating;
      if (accuracy !== undefined && accuracy > 0) {
        totalAccuracy += accuracy;
        count++;
      }
    });

    return count > 0 ? `${Math.round(totalAccuracy / count)}%` : 'N/A';
  };

  return (
    <div className="space-y-6">
      {/* Top Header Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 bg-secondary p-4 rounded-xl border border-border-gray shadow-sm">
        <div className="flex items-center gap-3">
          <button
            onClick={handleEndSession}
            className="flex items-center justify-center p-2 rounded-lg hover:bg-border-gray/30 text-muted-gray hover:text-primary transition-all"
            title="Exit Room"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div>
            <h2 className="text-base font-bold text-primary flex items-center gap-2">
              AI Copilot Console
            </h2>
            <p className="text-xs text-muted-gray select-all">Session ID: {id}</p>
          </div>
        </div>

        <div className="flex items-center gap-3 self-end sm:self-auto">
          {/* Status Indicator */}
          <div className="flex items-center gap-2 px-3 py-1.5 bg-white rounded-lg border border-border-gray">
            <span className={`h-2.5 w-2.5 rounded-full ${
              status === 'connected' ? 'bg-green-500 animate-pulse' :
              status === 'connecting' ? 'bg-amber-500 animate-pulse' : 'bg-muted-gray'
            }`} />
            <span className="text-xs font-bold capitalize text-primary">{status}</span>
          </div>

          {status === 'connected' ? (
            <button
              onClick={stopConnection}
              className="flex items-center gap-2 px-4 py-2 bg-red-50 hover:bg-red-100 text-red-600 text-xs font-bold rounded-lg border border-red-200 transition-colors"
            >
              <Power className="h-3.5 w-3.5" />
              Disconnect
            </button>
          ) : (
            <button
              onClick={startConnection}
              disabled={status === 'connecting'}
              className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary/95 text-white text-xs font-bold rounded-lg disabled:opacity-50 transition-colors shadow-sm"
            >
              <Mic className="h-3.5 w-3.5" />
              {status === 'connecting' ? 'Connecting...' : 'Connect Copilot'}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-600 rounded-lg text-xs font-semibold">
          Error: {error}
        </div>
      )}

      {/* Main Grid Workspace */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Left Column: Live Transcript & Input Feed */}
        <div className="lg:col-span-1 flex flex-col bg-secondary rounded-xl border border-border-gray shadow-sm h-[680px]">
          <div className="p-4 border-b border-border-gray">
            <h3 className="font-bold text-primary flex items-center gap-2 text-sm">
              <MessageSquare className="h-4 w-4 text-primary" />
              Live Interview Log
            </h3>
          </div>

          {/* Transcript Log Container */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {transcript.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center text-muted-gray p-6 gap-2">
                <MessageSquare className="h-10 w-10 stroke-[1.5]" />
                <p className="text-sm font-semibold">Ready for Conversation</p>
                <p className="text-xs max-w-[200px]">Send messages below or connect microphone stream to log conversation transcript.</p>
              </div>
            ) : (
              transcript.map((msg, index) => {
                const isCandidate = msg.speaker === 'Candidate';
                const isSystem = msg.speaker === 'System';

                if (isSystem) {
                  return (
                    <div key={index} className="flex justify-center">
                      <span className="text-[10px] font-bold bg-border-gray/50 text-muted-gray px-2.5 py-1 rounded-full uppercase tracking-wider">
                        {msg.text}
                      </span>
                    </div>
                  );
                }

                const isExpanded = expandedMessageIndex === index;

                return (
                  <div 
                    key={index} 
                    className={`flex flex-col gap-1.5 ${isCandidate ? 'items-start' : 'items-end'}`}
                  >
                    {/* Speaker name */}
                    <span className="text-[10px] font-bold text-muted-gray px-1">
                      {msg.speaker}
                    </span>

                    {/* Speech Bubble */}
                    <div className={`group relative rounded-xl p-3 max-w-[90%] border shadow-sm transition-all ${
                      isCandidate 
                        ? 'bg-white border-border-gray text-primary' 
                        : 'bg-primary text-white border-primary/30'
                    }`}>
                      <p className="text-xs leading-relaxed">{msg.text}</p>
                      
                      {/* Timestamp */}
                      <span className={`block text-[9px] mt-1.5 text-right ${
                        isCandidate ? 'text-muted-gray' : 'text-primary-foreground/75'
                      }`}>
                        {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>

                      {/* Expandable Evaluation Trigger for Candidate answers */}
                      {isCandidate && msg.evaluation && (
                        <button
                          onClick={() => toggleMessageExpand(index)}
                          className="mt-2 flex items-center gap-1 text-[10px] font-bold text-primary hover:underline border-t border-border-gray pt-2 w-full text-left"
                        >
                          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3.5 w-3.5" />}
                          {isExpanded ? 'Hide AI Assessment' : 'Show AI Assessment'}
                        </button>
                      )}
                    </div>

                    {/* Expandable Evaluation Metrics Drawer */}
                    {isCandidate && msg.evaluation && isExpanded && (
                      <div className="w-full bg-white rounded-lg p-3 border border-border-gray shadow-inner text-xs space-y-3">
                        <div className="grid grid-cols-2 gap-2.5">
                          {/* Metrics ratings */}
                          {[
                            { label: 'Technical Accuracy', val: msg.evaluation.technical_accuracy },
                            { label: 'Confidence', val: msg.evaluation.confidence },
                            { label: 'Completeness', val: msg.evaluation.completeness },
                            { label: 'Practical Skill', val: msg.evaluation.practical_knowledge },
                            { label: 'Communication', val: msg.evaluation.communication },
                            { label: 'Experience', val: msg.evaluation.production_experience }
                          ].map((item, mIdx) => {
                            if (!item.val) return null;
                            const score = item.val.rating;
                            const ratingColor = score >= 80 ? 'text-green-600 bg-green-50' : score >= 50 ? 'text-amber-600 bg-amber-50' : 'text-red-600 bg-red-50';
                            return (
                              <div key={mIdx} className="p-2 rounded-lg bg-secondary border border-border-gray/50">
                                <div className="flex items-center justify-between font-semibold mb-1">
                                  <span className="text-[10px] text-muted-gray">{item.label}</span>
                                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${ratingColor}`}>{score}%</span>
                                </div>
                                <p className="text-[10px] text-primary italic leading-tight">{item.val.comment}</p>
                              </div>
                            );
                          })}
                        </div>

                        {/* Missing concepts list */}
                        {msg.evaluation.missing_concepts && msg.evaluation.missing_concepts.length > 0 && (
                          <div className="border-t border-border-gray pt-2">
                            <span className="text-[10px] font-bold text-red-600 uppercase block mb-1">Missing Concepts</span>
                            <div className="flex flex-wrap gap-1">
                              {msg.evaluation.missing_concepts.map((concept, cIdx) => (
                                <span key={cIdx} className="px-2 py-0.5 bg-red-50 text-red-600 border border-red-100 rounded text-[9px] font-semibold">
                                  {concept}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Knowledge gaps list */}
                        {msg.evaluation.knowledge_gaps && msg.evaluation.knowledge_gaps.length > 0 && (
                          <div className="border-t border-border-gray pt-2">
                            <span className="text-[10px] font-bold text-amber-600 uppercase block mb-1">Identified Gaps</span>
                            <div className="flex flex-wrap gap-1">
                              {msg.evaluation.knowledge_gaps.map((gap, gIdx) => (
                                <span key={gIdx} className="px-2 py-0.5 bg-amber-50 text-amber-600 border border-amber-100 rounded text-[9px] font-semibold">
                                  {gap}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>

        </div>

        {/* Middle Column: Live Suggestions, Scenario Questions & Notes */}
        <div className="lg:col-span-1 flex flex-col gap-6">
          
          {/* Topic Banner Indicators */}
          <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm space-y-3">
            <div>
              <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1 tracking-wider">Current Discussion Topic</span>
              <div className="flex items-center gap-2 text-sm font-bold text-primary">
                <Activity className="h-4 w-4 text-green-500 shrink-0" />
                {intelligence.current_topic || 'No topic detected yet'}
              </div>
            </div>
            {assistance.recommended_next_topic && (
              <div className="pt-2.5 border-t border-border-gray/50">
                <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1 tracking-wider">Recommended Next Topic</span>
                <div className="flex items-center gap-2 text-xs font-bold text-primary bg-primary/5 p-2 rounded-lg border border-primary/10">
                  <Compass className="h-4 w-4 text-primary shrink-0 animate-spin-slow" />
                  {assistance.recommended_next_topic}
                </div>
              </div>
            )}
          </div>

          {/* AI Suggestions Box */}
          <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex-1 flex flex-col gap-4 min-h-[450px]">
            <h3 className="font-bold text-primary flex items-center gap-2 text-sm border-b border-border-gray pb-2.5">
              <User className="h-4 w-4 text-primary" />
              Real-time Suggestions
            </h3>

            <div className="flex-1 overflow-y-auto space-y-4 pr-1">
              {status !== 'connected' ? (
                <div className="h-full flex flex-col items-center justify-center text-center text-muted-gray py-12 gap-1.5">
                  <MessageSquare className="h-8 w-8 stroke-[1.5]" />
                  <p className="text-sm font-bold">Suggestions Stream Offline</p>
                  <p className="text-xs">Connect voice/text channel to start receiving live interviewer assistance tips.</p>
                </div>
              ) : (
                <>
                  {/* Suggested Follow-up Questions */}
                  {assistance.suggested_follow_up_questions && assistance.suggested_follow_up_questions.length > 0 && (
                    <div className="space-y-2">
                      <span className="text-[10px] font-bold text-primary uppercase tracking-wider block">Follow-up Questions</span>
                      <div className="space-y-1.5">
                        {assistance.suggested_follow_up_questions.map((q, idx) => (
                          <div key={idx} className="bg-white rounded-lg p-2.5 border border-border-gray/80 text-xs text-primary shadow-sm leading-relaxed">
                            {q}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Suggested Practical Scenario Questions */}
                  {assistance.suggested_practical_questions && assistance.suggested_practical_questions.length > 0 && (
                    <div className="space-y-2">
                      <span className="text-[10px] font-bold text-primary uppercase tracking-wider block">Practical & Scenario Questions</span>
                      <div className="space-y-1.5">
                        {assistance.suggested_practical_questions.map((q, idx) => (
                          <div key={idx} className="bg-white rounded-lg p-2.5 border border-border-gray/80 text-xs text-primary shadow-sm leading-relaxed border-l-4 border-l-primary">
                            {q}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Resume Verification Questions */}
                  {assistance.verification_questions && assistance.verification_questions.length > 0 && (
                    <div className="space-y-2">
                      <span className="text-[10px] font-bold text-primary uppercase tracking-wider block">Experience Verification Questions</span>
                      <div className="space-y-1.5">
                        {assistance.verification_questions.map((q, idx) => (
                          <div key={idx} className="bg-white rounded-lg p-2.5 border border-border-gray/80 text-xs text-primary shadow-sm leading-relaxed border-l-4 border-l-green-500">
                            {q}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Interview Notes */}
                  {assistance.interview_notes && assistance.interview_notes.length > 0 && (
                    <div className="space-y-2">
                      <span className="text-[10px] font-bold text-primary uppercase tracking-wider block">Observer Notes</span>
                      <ul className="space-y-1 bg-white rounded-lg p-3 border border-border-gray text-xs text-primary list-disc list-inside">
                        {assistance.interview_notes.map((note, idx) => (
                          <li key={idx} className="leading-relaxed mb-1">{note}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>

        {/* Right Column: Score, Understating, JD & Resume Coverage */}
        <div className="lg:col-span-1 flex flex-col gap-6">
          
          {/* Candidate Understanding Summary & Score */}
          <div className="grid grid-cols-3 gap-4">
            <div className="col-span-1 bg-secondary rounded-xl p-4 border border-border-gray shadow-sm text-center flex flex-col justify-center">
              <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">Accuracy Score</span>
              <span className="text-2xl font-black text-primary">{getCandidateScore()}</span>
            </div>
            
            <div className="col-span-2 bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col justify-center">
              <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">JD Coverage Progress</span>
              <div className="flex items-center gap-3">
                <div className="flex-1 h-3 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                  <div 
                    className="h-full bg-primary rounded-full transition-all duration-500" 
                    style={{ width: `${intelligence.interview_progress.percentage || 0}%` }}
                  />
                </div>
                <span className="text-xs font-black text-primary">{intelligence.interview_progress.percentage || 0}%</span>
              </div>
            </div>
          </div>

          {/* Candidate Understanding Summary */}
          {status === 'connected' && assistance.current_candidate_understanding && (
            <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm space-y-1.5">
              <span className="text-[10px] font-bold text-muted-gray uppercase block tracking-wider">Candidate Understanding Summary</span>
              <p className="text-xs text-primary leading-relaxed">{assistance.current_candidate_understanding}</p>
            </div>
          )}

          {/* JD Skill Coverage Panel */}
          <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
            <h3 className="font-bold text-primary flex items-center gap-2 text-sm border-b border-border-gray pb-2.5">
              <BookOpen className="h-4 w-4 text-primary" />
              JD Skill Requirements
            </h3>

            {status !== 'connected' ? (
              <p className="text-xs text-muted-gray text-center py-4">Coverage analysis offline.</p>
            ) : (
              <div className="space-y-3">
                {/* Covered Skills */}
                <div>
                  <span className="text-[10px] font-bold text-green-600 uppercase block mb-1.5 tracking-wider">Discussed / Covered</span>
                  <div className="flex flex-wrap gap-1.5">
                    {intelligence.covered_skills.length === 0 ? (
                      <span className="text-[10px] text-muted-gray italic">No skills covered yet</span>
                    ) : (
                      intelligence.covered_skills.map((skill, idx) => (
                        <span key={idx} className="flex items-center gap-1 px-2.5 py-1 bg-green-50 text-green-700 border border-green-200 rounded-lg text-[10px] font-bold shadow-sm">
                          <CheckCircle className="h-3 w-3 text-green-600 shrink-0" />
                          {skill}
                        </span>
                      ))
                    )}
                  </div>
                </div>

                {/* Remaining Skills */}
                <div className="border-t border-border-gray/50 pt-2.5">
                  <span className="text-[10px] font-bold text-amber-600 uppercase block mb-1.5 tracking-wider">Remaining / Uncovered</span>
                  <div className="flex flex-wrap gap-1.5">
                    {intelligence.remaining_skills.length === 0 ? (
                      <span className="text-[10px] text-muted-gray italic">All skills discussed!</span>
                    ) : (
                      intelligence.remaining_skills.map((skill, idx) => (
                        <span key={idx} className="px-2.5 py-1 bg-white text-muted-gray border border-border-gray rounded-lg text-[10px] font-bold shadow-sm">
                          {skill}
                        </span>
                      ))
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Resume Projects Coverage Panel */}
          <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
            <h3 className="font-bold text-primary flex items-center gap-2 text-sm border-b border-border-gray pb-2.5">
              <Briefcase className="h-4 w-4 text-primary" />
              Resume Experience Coverage
            </h3>

            {status !== 'connected' ? (
              <p className="text-xs text-muted-gray text-center py-4">Resume analysis offline.</p>
            ) : (
              <div className="space-y-3">
                {/* Covered Projects */}
                <div>
                  <span className="text-[10px] font-bold text-green-600 uppercase block mb-1.5 tracking-wider">Verified Projects</span>
                  <div className="flex flex-wrap gap-1.5">
                    {intelligence.resume_projects_covered.length === 0 ? (
                      <span className="text-[10px] text-muted-gray italic">No resume projects covered yet</span>
                    ) : (
                      intelligence.resume_projects_covered.map((proj, idx) => (
                        <span key={idx} className="flex items-center gap-1 px-2.5 py-1 bg-green-50 text-green-700 border border-green-200 rounded-lg text-[10px] font-bold shadow-sm">
                          <CheckCircle className="h-3 w-3 text-green-600 shrink-0" />
                          {proj}
                        </span>
                      ))
                    )}
                  </div>
                </div>

                {/* Remaining Projects */}
                <div className="border-t border-border-gray/50 pt-2.5">
                  <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1.5 tracking-wider">Unverified Projects</span>
                  <div className="flex flex-wrap gap-1.5">
                    {intelligence.resume_projects_remaining.length === 0 ? (
                      <span className="text-[10px] text-muted-gray italic">All projects verified!</span>
                    ) : (
                      intelligence.resume_projects_remaining.map((proj, idx) => (
                        <span key={idx} className="px-2.5 py-1 bg-white text-muted-gray border border-border-gray rounded-lg text-[10px] font-bold shadow-sm">
                          {proj}
                        </span>
                      ))
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>

        </div>
      </div>
    </div>
  );
};
