// src/components/ChatBox.jsx - WITH RENDERBLOCK ENVELOPE + THINKING PANEL SUPPORT
import React, { useEffect, useRef, useState } from 'react';
import RenderBlock from './RenderBlock';
import ClarificationForm from './ClarificationForm';
import ClarificationFileDownload from './ClarificationFileDownload';
import ThinkingPanel from './ThinkingPanel';
import AdminPanel from './AdminPanel';
import { secureFetch } from '../lib/api.js';

// Import logo from assets
import logo from '../assets/Optificial_logo.svg';

// Configuration
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8001';

// Agent icon mapping
const AGENT_ICONS = {
  invoice: '📄',
  schedule: '📅',
  workorder: '🔧',
};

/** Convert plain-text newlines to <br> so HTML rendering preserves line breaks. */
function nl2br(text) {
  if (!text) return text;
  return text.replace(/\n/g, '<br/>');
}

// Attachment Icon SVG Component
const AttachmentIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

// Microphone Icon SVG Component (minimal filled style)
const MicrophoneIcon = ({ recording }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="9" y="2" width="6" height="12" rx="3" fill={recording ? '#ef4444' : 'currentColor'} opacity={recording ? 1 : 0.85} />
    <path d="M5 11a7 7 0 0 0 14 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    <path d="M12 18v3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

export default function ChatBox({ user, token, onUserChange, onLogout }) {
  const [messages, setMessages] = useState([]);
  const [userInput, setUserInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [statusText, setStatusText] = useState('');
  const [streamingText, setStreamingText] = useState('');
  const [files, setFiles] = useState([]);

  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);

  // Generate a unique session ID for the active chat window instance
  const [chatSessionId] = useState(() => 'chat_sess_' + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15));

  // Theme: 'dark' | 'light'
  const [theme, setTheme] = useState(() => localStorage.getItem('optificial-theme') || 'dark');

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('optificial-theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark');

  // Admin panel
  const [showAdminPanel, setShowAdminPanel] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const profileMenuRef = useRef(null);

  // Close profile menu on outside click
  useEffect(() => {
    function handleClick(e) {
      if (profileMenuRef.current && !profileMenuRef.current.contains(e.target)) {
        setProfileMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Agent status panel
  const [agentPanelOpen, setAgentPanelOpen] = useState(false);
  const [agentStatus, setAgentStatus] = useState(null);
  const agentPanelRef = useRef(null);

  // Fetch agent/service status on mount + every 30s
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await secureFetch(`${BACKEND_URL}/api/agents/status`);
        setAgentStatus(await res.json());
      } catch { /* backend not reachable */ }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  // Close agent panel on outside click
  useEffect(() => {
    const handleClick = (e) => {
      if (agentPanelRef.current && !agentPanelRef.current.contains(e.target)) {
        setAgentPanelOpen(false);
      }
    };
    if (agentPanelOpen) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [agentPanelOpen]);

  // Agentic thinking panel
  const [thinkingPlan, setThinkingPlan] = useState(null);
  const thinkingPlanRef = useRef(null);

  const endRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);
  const abortRef = useRef(null);
  const streamingTextRef = useRef('');
  const streamingEnvelopeRef = useRef(null);
  const prevMessagesLenRef = useRef(0);

  useEffect(() => {
    const len = messages.length;
    const lastMsg = len > 0 ? messages[len - 1] : null;

    // Scroll to bottom when:
    // 1. User sends a new message (role === 'user')
    // 2. Typing indicator appears (isTyping)
    // 3. Streaming text is updating (active response)
    // Do NOT scroll when an assistant message (with data/tables) is added
    // — let the user see the top of the response first
    const isNewUserMessage = len > prevMessagesLenRef.current && lastMsg?.role === 'user';
    const shouldScroll = isNewUserMessage || isTyping || streamingText;

    if (shouldScroll) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
    prevMessagesLenRef.current = len;
  }, [messages, isTyping, streamingText]);

  // Cleanup mic stream on unmount
  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
      }
      if (stopTimeoutRef.current) {
        clearTimeout(stopTimeoutRef.current);
      }
    };
  }, []);

  const pushUser = (text, attachments = []) =>
    setMessages((prev) => [...prev, { role: 'user', content: text, attachments }]);

  const pushAssistant = (text, envelope = null, clarificationData = null, plan = null) =>
    setMessages((prev) => [...prev, { role: 'assistant', content: text, envelope, clarificationData, thinkingPlan: plan }]);

  function onPickFiles() {
    fileInputRef.current?.click();
  }

  function onFilesChosen(e) {
    const picked = Array.from(e.target.files || []);
    setFiles(prev => [...prev, ...picked]);
    e.target.value = "";
  }

  function removeFile(index) {
    setFiles(prev => prev.filter((_, i) => i !== index));
  }

  function handleCancel() {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }

  // ---- Voice recording (push-to-talk) ----
  // Keeps the mic stream alive between presses to eliminate start-lag.
  // Adds a short tail delay on stop to avoid clipping the last syllable.
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const streamRef = useRef(null);
  const mimeTypeRef = useRef('audio/webm');
  const stopTimeoutRef = useRef(null);
  const recordStartRef = useRef(null);

  async function startRecording() {
    // Clear any pending stop delay from a rapid re-press
    if (stopTimeoutRef.current) {
      clearTimeout(stopTimeoutRef.current);
      stopTimeoutRef.current = null;
    }

    try {
      // Reuse existing stream if still active (avoids getUserMedia lag)
      let stream = streamRef.current;
      if (!stream || !stream.active) {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });
        streamRef.current = stream;
      }

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : 'audio/mp4';
      mimeTypeRef.current = mimeType;

      const mediaRecorder = new MediaRecorder(stream, {
        mimeType,
        audioBitsPerSecond: 128000,
      });
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        // Release mic stream so browser mic indicator turns off
        if (streamRef.current) {
          streamRef.current.getTracks().forEach(track => track.stop());
          streamRef.current = null;
        }
        const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
        audioChunksRef.current = [];
        if (audioBlob.size === 0) return;
        // Skip transcription if recording was too short (likely accidental tap or silence)
        const duration = Date.now() - (recordStartRef.current || 0);
        if (duration < 500) return;
        await transcribeAudio(audioBlob, mimeType);
      };

      // timeslice=250ms flushes data every 250ms so nothing is lost
      mediaRecorder.start(250);
      recordStartRef.current = Date.now();
      setIsRecording(true);
    } catch (err) {
      console.error('[Voice] Microphone access error:', err);
      if (err.name === 'NotAllowedError') {
        pushAssistant('Microphone access was denied. Please allow microphone access in your browser settings and try again.');
      } else if (err.name === 'NotFoundError') {
        pushAssistant('No microphone found. Please connect a microphone and try again.');
      } else {
        pushAssistant(`Could not access microphone: ${err.message}`);
      }
    }
  }

  function stopRecording() {
    setIsRecording(false);
    // Small delay before actually stopping — captures the tail end of speech
    stopTimeoutRef.current = setTimeout(() => {
      stopTimeoutRef.current = null;
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
    }, 300);
  }

  async function transcribeAudio(audioBlob, mimeType) {
    setIsTranscribing(true);
    try {
      const formData = new FormData();
      const ext = mimeType.includes('mp4') ? 'mp4' : 'webm';
      formData.append('audio', audioBlob, `recording.${ext}`);

      const res = await secureFetch(`${BACKEND_URL}/api/transcribe`, {
        method: 'POST',
        body: formData,
      });

      const data = await res.json();
      const transcribedText = data.text || '';

      if (transcribedText.trim()) {
        setUserInput(prev => prev.trim() ? prev + ' ' + transcribedText : transcribedText);
        inputRef.current?.focus();
      }
    } catch (err) {
      console.error('[Voice] Transcription error:', err);
      pushAssistant(`Voice transcription failed: ${err.message}`);
    } finally {
      setIsTranscribing(false);
    }
  }

  async function handleSend() {
    if (isTyping) return; // prevent double-send
    if (!userInput.trim() && files.length === 0) return;

    // Separate audio files from non-audio files
    const audioFiles = files.filter(f =>
      f.type.startsWith('audio/') ||
      /\.(mp3|wav|webm|m4a|ogg|flac)$/i.test(f.name)
    );
    const nonAudioFiles = files.filter(f => !audioFiles.includes(f));

    let text = userInput;

    // Auto-transcribe audio files before sending
    if (audioFiles.length > 0) {
      setIsTranscribing(true);
      try {
        for (const audioFile of audioFiles) {
          const formData = new FormData();
          formData.append('audio', audioFile, audioFile.name);
          const res = await secureFetch(`${BACKEND_URL}/api/transcribe`, {
            method: 'POST',
            body: formData,
          });
          const data = await res.json();
          if (data.text?.trim()) {
            text = text.trim() ? text + ' ' + data.text : data.text;
          }
        }
      } catch (err) {
        console.error('[Voice] File transcription error:', err);
        pushAssistant(`Voice transcription failed: ${err.message}`);
        setIsTranscribing(false);
        return;
      } finally {
        setIsTranscribing(false);
      }
    }

    if (!text.trim() && nonAudioFiles.length === 0) return;

    const filesSnapshot = [...nonAudioFiles];
    const currentAttachments = [
      ...audioFiles.map(f => ({ name: f.name, size: f.size, type: f.type })),
      ...filesSnapshot.map(f => ({ name: f.name, size: f.size, type: f.type })),
    ];

    pushUser(text, currentAttachments);
    setUserInput('');
    setFiles([]);
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      inputRef.current.classList.remove('scrollable');
    }

    setIsTyping(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const formData = new FormData();
      formData.append('message', text);
      formData.append('session_id', chatSessionId);

      for (const f of filesSnapshot) {
        formData.append('files', f);
      }

      const res = await secureFetch(`${BACKEND_URL}/api/chat/stream`, {
        method: 'POST',
        body: formData,
        signal: controller.signal
      });

      // Read SSE stream — word-by-word token streaming
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let gotResult = false; // tracks if a 'result' event was received (clarification/error)

      // Reset streaming refs
      streamingTextRef.current = '';
      streamingEnvelopeRef.current = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop(); // keep incomplete chunk

        for (const part of parts) {
          if (!part.trim()) continue;

          const lines = part.split('\n');
          let eventType = '';
          let dataStr = '';

          for (const line of lines) {
            if (line.startsWith('event: ')) eventType = line.slice(7).trim();
            else if (line.startsWith('data: ')) dataStr = line.slice(6);
          }

          if (!eventType || !dataStr) continue;

          if (eventType === 'thinking') {
            // Agentic plan progress update
            const payload = JSON.parse(dataStr);
            thinkingPlanRef.current = payload.plan || null;
            setThinkingPlan(payload.plan || null);

          } else if (eventType === 'status') {
            const payload = JSON.parse(dataStr);
            setStatusText(payload.message || '');

          } else if (eventType === 'token') {
            // Word-by-word streaming — accumulate text
            const payload = JSON.parse(dataStr);
            const chunk = payload.text || '';
            streamingTextRef.current += chunk;
            setStreamingText(prev => prev + chunk);
            setStatusText(''); // clear status once tokens start flowing

          } else if (eventType === 'envelope') {
            // Envelope arrived — store for finalization
            const payload = JSON.parse(dataStr);
            streamingEnvelopeRef.current = payload.envelope || null;

          } else if (eventType === 'result') {
            // Non-streamable path (clarification, errors) — push directly
            gotResult = true;
            const data = JSON.parse(dataStr);
            const reply = data?.response || data?.reply || 'No response from server';
            const envelope = data?.envelope || null;
            const needsClarification = data?.needs_clarification || false;
            const clarificationData = needsClarification ? data?.clarification_data : null;
            pushAssistant(reply, envelope, clarificationData, thinkingPlanRef.current);

          } else if (eventType === 'done') {
            // Capture final plan snapshot from done event
            try {
              const doneData = JSON.parse(dataStr);
              if (doneData.plan) thinkingPlanRef.current = doneData.plan;
            } catch (_) { /* ignore parse errors on empty done */ }
            break;
          }
        }
      }

      // Finalize streamed message (if we didn't get a direct 'result' event)
      if (!gotResult) {
        const finalText = streamingTextRef.current;
        const finalEnvelope = streamingEnvelopeRef.current;
        const finalPlan = thinkingPlanRef.current;
        if (finalText || finalEnvelope) {
          pushAssistant(finalText, finalEnvelope, null, finalPlan);
        }
      }

    } catch (err) {
      if (err.name === 'AbortError') {
        pushAssistant('Query cancelled.');
      } else {
        console.error('[ChatBox] Error:', err);
        pushAssistant(`⚠️ Error: ${err.message}. Make sure the backend is running on ${BACKEND_URL}`);
      }
    } finally {
      abortRef.current = null;
      setStatusText('');
      setStreamingText('');
      setThinkingPlan(null);
      setIsTyping(false);
      streamingTextRef.current = '';
      streamingEnvelopeRef.current = null;
      thinkingPlanRef.current = null;
    }
  }

  function handleKeyPress(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!isTyping) handleSend();
    }
  }

  async function handleClarificationSubmit(payload) {
    // User has fixed clarifications - resubmit to agent via SSE stream
    setIsTyping(true);
    setStatusText('Processing your selections...');

    pushUser(payload._summary || `[Clarifications submitted for ${Object.keys(payload.clarifications).length} rows]`);

    // Reset streaming refs for this clarification round
    streamingTextRef.current = '';
    streamingEnvelopeRef.current = null;
    thinkingPlanRef.current = null;
    setStreamingText('');
    setThinkingPlan(null);

    try {
      const agent = payload._agent || 'schedule';
      // Contradiction resolutions use a dedicated endpoint
      const isContradiction = payload._contradiction;
      const endpoint = isContradiction
        ? `${BACKEND_URL}/api/contradiction/clarify/${payload.session_id}`
        : `${BACKEND_URL}/api/${agent}/clarify/${payload.session_id}`;
      const res = await secureFetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      const contentType = res.headers.get('content-type') || '';

      // SSE stream path (schedule/workorder/invoice clarify endpoints)
      if (contentType.includes('text/event-stream')) {
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let gotResult = false;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n\n');
          buffer = parts.pop();

          for (const part of parts) {
            if (!part.trim()) continue;

            const lines = part.split('\n');
            let eventType = '';
            let dataStr = '';

            for (const line of lines) {
              if (line.startsWith('event: ')) eventType = line.slice(7).trim();
              else if (line.startsWith('data: ')) dataStr = line.slice(6);
            }

            if (!eventType || !dataStr) continue;

            if (eventType === 'thinking') {
              const p = JSON.parse(dataStr);
              thinkingPlanRef.current = p.plan || null;
              setThinkingPlan(p.plan || null);

            } else if (eventType === 'status') {
              const p = JSON.parse(dataStr);
              setStatusText(p.message || '');

            } else if (eventType === 'token') {
              const p = JSON.parse(dataStr);
              const chunk = p.text || '';
              streamingTextRef.current += chunk;
              setStreamingText(prev => prev + chunk);
              setStatusText('');

            } else if (eventType === 'envelope') {
              const p = JSON.parse(dataStr);
              streamingEnvelopeRef.current = p.envelope || null;

            } else if (eventType === 'result') {
              gotResult = true;
              const data = JSON.parse(dataStr);
              const reply = data?.response || data?.reply || 'No response from server';
              const envelope = data?.envelope || null;
              const needsClarification = data?.needs_clarification || false;
              const clarificationData = needsClarification ? data?.clarification_data : null;
              pushAssistant(reply, envelope, clarificationData, thinkingPlanRef.current);

            } else if (eventType === 'done') {
              try {
                const doneData = JSON.parse(dataStr);
                if (doneData.plan) thinkingPlanRef.current = doneData.plan;
              } catch (_) { /* ignore parse errors */ }
              break;
            }
          }
        }

        // Finalize streamed message (if we didn't get a direct 'result' event)
        if (!gotResult) {
          const finalText = streamingTextRef.current;
          const finalEnvelope = streamingEnvelopeRef.current;
          const finalPlan = thinkingPlanRef.current;
          if (finalText || finalEnvelope) {
            pushAssistant(finalText, finalEnvelope, null, finalPlan);
          }
        }
      } else {
        // Fallback: plain JSON response (contradiction endpoint or legacy)
        const data = await res.json();
        const reply = data?.response || data?.reply || 'Clarifications processed';
        const envelope = data?.envelope || null;
        const needsClarification = data?.needs_clarification || false;
        const clarificationData = needsClarification ? data?.clarification_data : null;

        pushAssistant(reply, envelope, clarificationData);
      }
    } catch (err) {
      console.error('[ClarificationSubmit] Error:', err);
      pushAssistant(`⚠️ Error submitting clarifications: ${err.message}`);
    } finally {
      setStatusText('');
      setStreamingText('');
      setThinkingPlan(null);
      setIsTyping(false);
      streamingTextRef.current = '';
      streamingEnvelopeRef.current = null;
      thinkingPlanRef.current = null;
    }
  }

  function handleClarificationCancel() {
    pushAssistant('Clarification cancelled. Please re-upload your file with corrections.');
  }

  return (
    <div className="app-shell">
      {/* Floating Particles Background */}
      <div className="particles">
        <div className="particle" style={{ left: '10%', animationDelay: '0s' }}></div>
        <div className="particle" style={{ left: '20%', animationDelay: '2s' }}></div>
        <div className="particle" style={{ left: '30%', animationDelay: '4s' }}></div>
        <div className="particle" style={{ left: '40%', animationDelay: '1s' }}></div>
        <div className="particle" style={{ left: '50%', animationDelay: '3s' }}></div>
        <div className="particle" style={{ left: '60%', animationDelay: '5s' }}></div>
        <div className="particle" style={{ left: '70%', animationDelay: '2.5s' }}></div>
        <div className="particle" style={{ left: '80%', animationDelay: '4.5s' }}></div>
        <div className="particle" style={{ left: '90%', animationDelay: '1.5s' }}></div>
      </div>

      {/* Top Navigation */}
      <nav className="top-nav">
        <div className="nav-container">
          <div className="nav-left">
            {/* Logo with imported image */}
            <div className="nav-logo">
              <img src={logo} alt="Optificial" className="logo-image" />
              <span className="logo-text">Optificial.AI</span>
            </div>
            
            {/* Navigation Links */}
            <div className="nav-links">
              <button className="nav-link">Projects</button>
              <button className="nav-link">Financials</button>
              <button className="nav-link">Analytics</button>
              <button className="nav-link">
                <svg style={{ width: '16px', height: '16px', display: 'inline', marginRight: '4px' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                </svg>
                History
              </button>
            </div>
          </div>
          
          <div className="nav-right">
            {/* Agent Status */}
            <div className="agent-status-wrapper" ref={agentPanelRef}>
              <button
                className={`agent-status-btn${agentPanelOpen ? ' active' : ''}`}
                onClick={() => setAgentPanelOpen(prev => !prev)}
              >
                <div className="agent-dots">
                  <div className="agent-dot"></div>
                  <div className="agent-dot"></div>
                  <div className="agent-dot"></div>
                </div>
                <span>{agentStatus ? `${agentStatus.agents?.length || 0} Agents Active` : '...'}</span>
                <svg className={`agent-chevron${agentPanelOpen ? ' open' : ''}`} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="6 9 12 15 18 9"/>
                </svg>
              </button>

              {agentPanelOpen && agentStatus && (
                <div className="agent-panel">
                  <div className="agent-panel-header">
                    <span className="agent-panel-title">Active Agents</span>
                    <span className={`agent-panel-badge ${(agentStatus.agents?.length || 0) > 0 ? 'healthy' : 'offline'}`}>
                      {agentStatus.agents?.length || 0} Online
                    </span>
                  </div>

                  {agentStatus.agents?.length > 0 && (
                    <div className="agent-panel-section">
                      {agentStatus.agents.map(ag => (
                        <div key={ag.id} className="agent-panel-item">
                          <span className="agent-panel-icon">{AGENT_ICONS[ag.id] || '🤖'}</span>
                          <div className="agent-panel-info">
                            <span className="agent-panel-name">{ag.title}</span>
                            <span className="agent-panel-desc">{ag.responsibility}</span>
                          </div>
                          <span className={`agent-panel-dot ${ag.status}`} title={ag.status}/>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Theme Toggle */}
            <button className="theme-toggle-btn" onClick={toggleTheme} title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              {theme === 'dark' ? (
                /* Sun icon */
                <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="5" strokeWidth="2" strokeLinecap="round"/>
                  <path strokeWidth="2" strokeLinecap="round" d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
                </svg>
              ) : (
                /* Moon icon */
                <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
                </svg>
              )}
            </button>

            {/* Profile dropdown */}
            <div className="profile-menu-wrap" ref={profileMenuRef}>
              <div
                className="profile-avatar"
                style={{ cursor: 'pointer' }}
                onClick={() => setProfileMenuOpen(v => !v)}
                title="Account"
              >
                {user?.name
                  ? user.name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
                  : (user?.email || '?')[0].toUpperCase()}
              </div>

              {profileMenuOpen && (
                <div className="profile-dropdown">
                  <div className="profile-dropdown-info">
                    <div className="profile-dropdown-name">{user?.name || user?.email}</div>
                    <div className="profile-dropdown-email">{user?.email}</div>
                    {user?.role && (
                      <span className="admin-badge admin-badge-indigo" style={{ marginTop: 4, display: 'inline-block' }}>
                        {user.role.charAt(0).toUpperCase() + user.role.slice(1)}
                      </span>
                    )}
                  </div>
                  <div className="profile-dropdown-divider" />
                  {user?.role === 'admin' && (
                    <button
                      className="profile-dropdown-item"
                      onClick={() => { setShowAdminPanel(true); setProfileMenuOpen(false); }}
                    >
                      <svg width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" style={{ marginRight: 8 }}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                      </svg>
                      Manage Team
                    </button>
                  )}
                  <button className="profile-dropdown-item profile-dropdown-logout" onClick={onLogout}>
                    <svg width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" style={{ marginRight: 8 }}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
                    </svg>
                    Sign Out
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main>
        <div id="chatContainer">
          {/* Welcome State */}
          {messages.length === 0 && (
            <div className="welcome-state">
              <h1 className="welcome-title">Hello Welcome</h1>
              <p className="welcome-subtitle">Ask me anything about your projects, financials, or productivity</p>
              
              <div className="welcome-cards">
                <div className="welcome-card">
                  <div className="card-icon" style={{ color: '#06b6d4' }}>
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path>
                    </svg>
                  </div>
                  <p className="card-title">Financial Overview</p>
                  <p className="card-description">View revenue and profitability</p>
                </div>
                
                <div className="welcome-card">
                  <div className="card-icon" style={{ color: '#a855f7' }}>
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                    </svg>
                  </div>
                  <p className="card-title">Generate Documents</p>
                  <p className="card-description">Create invoices and work orders</p>
                </div>
                
                <div className="welcome-card">
                  <div className="card-icon" style={{ color: '#ec4899' }}>
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                  </div>
                  <p className="card-title">Track Receivables</p>
                  <p className="card-description">Monitor outstanding payments</p>
                </div>
              </div>
            </div>
          )}
          
          {/* Messages */}
          <div className="messages">
            {messages.map((m, i) => (
              <ChatMessage
                key={i}
                message={m}
                onClarificationSubmit={handleClarificationSubmit}
                onClarificationCancel={handleClarificationCancel}
                backendUrl={BACKEND_URL}
              />
            ))}
            
            {isTyping && (
              <div className="message assistant">
                <div className="message-bubble assistant">
                  <div className="ai-header">
                    <img src={logo} alt="AI" className="ai-icon" />
                    <span className="ai-label">Optificial AI</span>
                  </div>

                  {/* Agentic thinking panel (shown when plan exists) */}
                  {thinkingPlan && (
                    <ThinkingPanel
                      plan={thinkingPlan}
                      collapsed={!!streamingText}
                    />
                  )}

                  {streamingText ? (
                    <div
                      className="html-content streaming-text"
                      dangerouslySetInnerHTML={{ __html: nl2br(streamingText) + '<span class="stream-cursor"></span>' }}
                    />
                  ) : !thinkingPlan ? (
                    <LoadingStatus serverStatus={statusText} />
                  ) : null}
                </div>
              </div>
            )}
          </div>
          <div ref={endRef} />
        </div>
      </main>

      {/* Command Bar (Bottom) */}
      <div className="command-bar">
        <div className="command-container">
          {/* File Preview (now in command bar) */}
          {files.length > 0 && (
            <div className="file-preview">
              <div className="file-preview-inner">
                {files.map((file, i) => (
                  <div key={i} className="file-preview-item">
                    <span className="file-name">{file.name}</span>
                    <button onClick={() => removeFile(i)} className="file-remove">×</button>
                  </div>
                ))}
              </div>
            </div>
          )}
          
          <div className="command-row">
            <div className="command-input-wrapper">
              <textarea
                ref={inputRef}
                className="command-input"
                rows={1}
                placeholder="Ask about financials, create documents, or run analytics..."
                value={userInput}
                onChange={(e) => {
                  setUserInput(e.target.value);
                  // Auto-resize up to 5 lines, then scroll
                  e.target.style.height = 'auto';
                  const scrollH = e.target.scrollHeight;
                  e.target.style.height = Math.min(scrollH, 120) + 'px';
                  // Show scrollbar only when content exceeds max height
                  e.target.classList.toggle('scrollable', scrollH > 120);
                }}
                onKeyDown={handleKeyPress}
              />
              <span className="input-hint"></span>
            </div>

            {/* Attachment Button */}
            <button className="attach-btn" onClick={onPickFiles} title="Attach files">
              <AttachmentIcon />
            </button>

            {/* Microphone Button (Push-to-Talk) */}
            <button
              className={`mic-btn${isRecording ? ' is-recording' : ''}${isTranscribing ? ' is-transcribing' : ''}`}
              onMouseDown={!isTyping && !isTranscribing ? startRecording : undefined}
              onMouseUp={isRecording ? stopRecording : undefined}
              onMouseLeave={isRecording ? stopRecording : undefined}
              onTouchStart={!isTyping && !isTranscribing ? (e) => { e.preventDefault(); startRecording(); } : undefined}
              onTouchEnd={isRecording ? (e) => { e.preventDefault(); stopRecording(); } : undefined}
              disabled={isTyping || isTranscribing}
              title={isRecording ? 'Recording... release to stop' : isTranscribing ? 'Transcribing...' : 'Hold to speak'}
            >
              {isTranscribing ? <div className="spinner" /> : <MicrophoneIcon recording={isRecording} />}
            </button>

            {isTyping ? (
              <button
                className="send-btn is-loading"
                onClick={handleCancel}
                title="Cancel query"
              >
                <div className="spinner" />
                <span>Stop</span>
              </button>
            ) : (
              <button
                className="send-btn"
                onClick={handleSend}
                disabled={!userInput.trim() && files.length === 0}
              >
                <span>Send</span>
                <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path>
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>

      <input
        type="file"
        ref={fileInputRef}
        style={{ display: 'none' }}
        multiple
        accept=".xlsx,.xls,.csv,.txt,.pdf,.doc,.docx,.mp3,.wav,.webm,.m4a,.ogg,.flac"
        onChange={onFilesChosen}
      />

      {/* Admin Panel (Phase 4) */}
      {showAdminPanel && (
        <AdminPanel
          token={token}
          currentUser={user}
          onUserChange={onUserChange}
          onClose={() => setShowAdminPanel(false)}
        />
      )}
    </div>
  );
}

const LOADING_MESSAGES = [
  "Understanding your request...",
  "Thinking...",
  "Processing data...",
  "Working on it...",
  "Almost there...",
];

function LoadingStatus({ serverStatus }) {
  const [index, setIndex] = useState(0);
  const [displayText, setDisplayText] = useState(LOADING_MESSAGES[0]);
  const lastServerStatus = useRef('');
  const serverStatusTimer = useRef(null);
  const isServerActive = useRef(false);

  useEffect(() => {
    // When a real SSE status arrives, show it immediately and pause cycling
    if (serverStatus && serverStatus !== lastServerStatus.current) {
      lastServerStatus.current = serverStatus;
      isServerActive.current = true;
      setDisplayText(serverStatus);

      clearTimeout(serverStatusTimer.current);
      serverStatusTimer.current = setTimeout(() => {
        isServerActive.current = false;
        lastServerStatus.current = '';
      }, 3000);
      return;
    }
  }, [serverStatus]);

  useEffect(() => {
    // Auto-cycle messages every 2s when no real server status is active
    const interval = setInterval(() => {
      if (!isServerActive.current) {
        setIndex(prev => {
          const next = (prev + 1) % LOADING_MESSAGES.length;
          setDisplayText(LOADING_MESSAGES[next]);
          return next;
        });
      }
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    return () => clearTimeout(serverStatusTimer.current);
  }, []);

  return (
    <div className="loading-status">
      <div className="loading-spinner" />
      <span className="loading-text" key={displayText}>{displayText}</span>
    </div>
  );
}

function ChatMessage({ message, onClarificationSubmit, onClarificationCancel, backendUrl }) {
  const isUser = message.role === 'user';
  const hasClarification = !isUser && message.clarificationData;

  return (
    <div className={`message ${isUser ? 'user' : 'assistant'}`}>
      <div className={`message-bubble ${isUser ? 'user' : 'assistant'}${!isUser && (message.envelope || hasClarification) ? ' has-envelope' : ''}`}>
        {!isUser && (
          <div className="ai-header">
            <div className="ai-icon"></div>
            <span className="ai-label">Optificial AI</span>
          </div>
        )}

        {/* Persisted thinking panel for historical messages */}
        {!isUser && message.thinkingPlan && (
          <ThinkingPanel plan={message.thinkingPlan} collapsed={true} />
        )}

        {/* Render based on message type */}
        {isUser ? (
          <div style={{ whiteSpace: 'pre-wrap' }}>{message.content}</div>
        ) : hasClarification ? (
          <>
            {/* Show message text */}
            <div className="clarification-intro">{message.content}</div>

            {/* Show completed results table above clarification (multi-action) */}
            {message.envelope && <RenderBlock envelope={message.envelope} />}

            {/* Show appropriate clarification component */}
            {message.clarificationData.clarification_mode === 'file_download' ? (
              <ClarificationFileDownload
                clarificationData={message.clarificationData}
                backendUrl={backendUrl}
              />
            ) : (
              <ClarificationForm
                clarificationData={message.clarificationData}
                onSubmit={onClarificationSubmit}
                onCancel={onClarificationCancel}
              />
            )}
          </>
        ) : message.envelope ? (
          <RenderBlock envelope={message.envelope} />
        ) : (
          <div
            className="html-content"
            dangerouslySetInnerHTML={{ __html: nl2br(message.content) }}
          />
        )}

        {message.attachments && message.attachments.length > 0 && (
          <div className="message-attachments">
            {message.attachments.map((a, i) => (
              <div key={i} className="attach-pill">
                <span>{a.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}