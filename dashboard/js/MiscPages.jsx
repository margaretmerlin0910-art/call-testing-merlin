// Shared misc pages: outbound calls, language presets, demo link

function OutboundPage() {
  const [mode, setMode] = React.useState('single');
  const [phone, setPhone] = React.useState('');
  const [name, setName] = React.useState('');
  const [bulkList, setBulkList] = React.useState('');
  const [dispatching, setDispatching] = React.useState(false);
  const [dispatchState, setDispatchState] = React.useState('idle'); // idle | ok | error
  const [dispatchMsg, setDispatchMsg] = React.useState('');
  const [jobs, setJobs] = React.useState([]);
  const [livekit, setLivekit] = React.useState({ url: '', sip_trunk_id: '', connected: false });

  React.useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(data => {
      setLivekit({
        url: data.livekit_url || '-',
        sip_trunk_id: data.sip_trunk_id || '-',
        connected: !!(data.livekit_url && data.livekit_api_key && data.sip_trunk_id),
      });
    }).catch(() => {});

    fetch('/api/logs').then(r => r.json()).then(data => {
      setJobs(Array.isArray(data) ? data.slice(0, 20) : []);
    }).catch(() => {});
  }, []);

  const handleDispatch = async () => {
    setDispatching(true);
    setDispatchState('idle');
    try {
      if (mode === 'single') {
        const res = await fetch('/api/call/single', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone, caller_name: name }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || 'Dispatch failed');
        setDispatchState('ok');
        setDispatchMsg(`Call dispatched to ${phone}`);
        setPhone('');
        setName('');
      } else {
        const lines = bulkList.trim().split('\n').filter(Boolean);
        const numbersStr = lines.map(l => l.split(',')[0].trim()).join('\n');
        const res = await fetch('/api/call/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ numbers: numbersStr }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || 'Bulk dispatch failed');
        setDispatchState('ok');
        setDispatchMsg(`${lines.length} calls queued`);
        setBulkList('');
      }
    } catch (e) {
      setDispatchState('error');
      setDispatchMsg(e.message);
    } finally {
      setDispatching(false);
      setTimeout(() => {
        setDispatchState('idle');
        setDispatchMsg('');
      }, 4000);
    }
  };

  const RESULT_C = { booked: 'green', failed: 'red', completed: 'blue', pending: 'amber', unknown: 'gray' };

  return (
    <div>
      <C.PageHeader title="Outbound Calls" sub="Trigger single or bulk outbound calls via the voice agent" />
      <div style={{ display: 'grid', gridTemplateColumns: '400px 1fr', gap: 20 }}>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <C.Card style={{ padding: '20px' }}>
            <div style={{ marginBottom: 16 }}>
              <C.Tabs tabs={['single', 'bulk']} active={mode} onChange={setMode} />
            </div>
            {mode === 'single' ? (
              <div style={{ display: 'grid', gap: 14 }}>
                <C.Input label="Phone Number" value={phone} onChange={setPhone} placeholder="+91 98765 43210" />
                <C.Input label="Contact Name (optional)" value={name} onChange={setName} placeholder="Full name" />
                <C.Btn variant="primary" onClick={handleDispatch} disabled={!phone || dispatching}>
                  {dispatching ? 'Dispatching...' : 'Dispatch Call'}
                </C.Btn>
              </div>
            ) : (
              <div style={{ display: 'grid', gap: 14 }}>
                <C.Input
                  label="Phone Numbers"
                  value={bulkList}
                  onChange={setBulkList}
                  rows={8}
                  placeholder={"+91 98765 43210, Rohan Mehta\n+91 87654 32109, Priya Sharma\n..."}
                  hint="One per line: phone, name (name optional)"
                />
                <div style={{ fontSize: 12, color: '#7b849a' }}>
                  {bulkList.trim() ? `${bulkList.trim().split('\n').filter(Boolean).length} numbers detected` : 'Paste phone numbers above'}
                </div>
                <C.Btn variant="primary" onClick={handleDispatch} disabled={!bulkList.trim() || dispatching}>
                  {dispatching ? 'Queuing...' : `Dispatch Bulk (${bulkList.trim() ? bulkList.trim().split('\n').filter(Boolean).length : 0})`}
                </C.Btn>
              </div>
            )}
            {dispatchMsg && (
              <div style={{ marginTop: 12, fontSize: 13, padding: '8px 12px', borderRadius: 8, background: dispatchState === 'ok' ? 'rgba(52,211,153,0.1)' : 'rgba(248,113,113,0.1)', color: dispatchState === 'ok' ? '#34d399' : '#f87171', border: `1px solid ${dispatchState === 'ok' ? 'rgba(52,211,153,0.2)' : 'rgba(248,113,113,0.2)'}` }}>
                {dispatchMsg}
              </div>
            )}
          </C.Card>

          <C.Card style={{ padding: '16px 20px' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#7b849a', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 12 }}>Trunk Status</div>
            {[
              { label: 'LiveKit URL', value: livekit.url },
              { label: 'SIP Trunk', value: livekit.sip_trunk_id },
              { label: 'Status', value: <C.Badge color={livekit.connected ? 'green' : 'red'}>{livekit.connected ? 'Connected' : 'Not configured'}</C.Badge> },
            ].map(r => (
              <div key={r.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <span style={{ color: '#7b849a' }}>{r.label}</span>
                <span style={{ color: '#e8eaef', fontWeight: 600, fontFamily: 'monospace', fontSize: 11, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.value}</span>
              </div>
            ))}
          </C.Card>
        </div>

        <C.Card title="Recent Call History">
          <C.Table
            cols={['Phone / Name', 'Time', 'Duration', 'Status']}
            empty="No calls yet."
            rows={jobs.map(j => [
              <div>
                <div style={{ fontWeight: 600, color: '#e8eaef', fontVariantNumeric: 'tabular-nums' }}>{j.phone_number}</div>
                <div style={{ fontSize: 11, color: '#7b849a', marginTop: 2 }}>{j.caller_name || 'Unknown'}</div>
              </div>,
              <span style={{ fontSize: 12, color: '#7b849a' }}>{j.created_at ? new Date(j.created_at).toLocaleString('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true }) : '-'}</span>,
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{j.duration_seconds != null ? `${Math.floor(j.duration_seconds / 60)}m ${Math.round(j.duration_seconds % 60)}s` : '-'}</span>,
              <C.Badge color={RESULT_C[j.status] || 'gray'}>{j.status || 'unknown'}</C.Badge>,
            ])}
          />
        </C.Card>
      </div>
    </div>
  );
}

const PRESETS = [
  { id: 'multilingual', label: 'Multilingual (Auto-detect)', desc: 'Detect the caller language and adapt naturally across Indian campaigns.', sttLanguage: 'unknown', ttsLanguage: 'hi-IN', ttsVoice: 'kavya', geminiLiveLanguage: '' },
  { id: 'hinglish', label: 'Hinglish', desc: 'Mix Hindi and English naturally for conversational Indian sales calls.', sttLanguage: 'hi-IN', ttsLanguage: 'hi-IN', ttsVoice: 'kavya', geminiLiveLanguage: '' },
  { id: 'english', label: 'English (India)', desc: 'Indian English only. Fast and consistent for urban prospects.', sttLanguage: 'en-IN', ttsLanguage: 'en-IN', ttsVoice: 'dev', geminiLiveLanguage: 'en-IN' },
  { id: 'hindi', label: 'Hindi', desc: 'Pure Hindi. Best for Tier 2/3 markets.', sttLanguage: 'hi-IN', ttsLanguage: 'hi-IN', ttsVoice: 'ritu', geminiLiveLanguage: 'hi-IN' },
  { id: 'marathi', label: 'Marathi', desc: 'Polite Marathi for Maharashtra-focused outreach.', sttLanguage: 'mr-IN', ttsLanguage: 'mr-IN', ttsVoice: 'shubh', geminiLiveLanguage: 'mr-IN' },
  { id: 'tamil', label: 'Tamil', desc: 'Tamil language for Tamil Nadu campaigns.', sttLanguage: 'ta-IN', ttsLanguage: 'ta-IN', ttsVoice: 'priya', geminiLiveLanguage: 'ta-IN' },
  { id: 'telugu', label: 'Telugu', desc: 'Telugu for Hyderabad and Andhra campaigns.', sttLanguage: 'te-IN', ttsLanguage: 'te-IN', ttsVoice: 'kavya', geminiLiveLanguage: 'te-IN' },
  { id: 'gujarati', label: 'Gujarati', desc: 'Gujarati for western India campaigns and investor outreach.', sttLanguage: 'gu-IN', ttsLanguage: 'gu-IN', ttsVoice: 'rohan', geminiLiveLanguage: '' },
  { id: 'bengali', label: 'Bengali', desc: 'Bengali for Kolkata and east India campaigns.', sttLanguage: 'bn-IN', ttsLanguage: 'bn-IN', ttsVoice: 'neha', geminiLiveLanguage: '' },
  { id: 'kannada', label: 'Kannada', desc: 'Kannada for Bengaluru and Karnataka outreach.', sttLanguage: 'kn-IN', ttsLanguage: 'kn-IN', ttsVoice: 'rahul', geminiLiveLanguage: '' },
  { id: 'malayalam', label: 'Malayalam', desc: 'Malayalam for Kerala prospects and follow-ups.', sttLanguage: 'ml-IN', ttsLanguage: 'ml-IN', ttsVoice: 'ritu', geminiLiveLanguage: '' },
];

function LanguagePage() {
  const [active, setActive] = React.useState('multilingual');
  const [saved, setSaved] = React.useState(false);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(cfg => {
        const presetId = PRESETS.some(p => p.id === cfg.lang_preset) ? cfg.lang_preset : 'multilingual';
        setActive(presetId);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    try {
      const preset = PRESETS.find(p => p.id === active) || PRESETS[0];
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lang_preset: preset.id,
          stt_language: preset.sttLanguage,
          tts_language: preset.ttsLanguage,
          tts_voice: preset.ttsVoice,
          gemini_live_language: preset.geminiLiveLanguage,
        }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {}
  };

  return (
    <div>
      <C.PageHeader title="Language Preset" sub="Select the language configuration for this agent" />
      {loading ? <C.Spinner /> : (
        <div style={{ display: 'grid', gap: 12, maxWidth: 600 }}>
          {PRESETS.map(p => (
            <div key={p.id} onClick={() => setActive(p.id)}
              style={{ border: `1px solid ${active === p.id ? '#5a7ef5' : 'rgba(255,255,255,0.07)'}`, borderRadius: 12, padding: '16px 20px', cursor: 'pointer', background: active === p.id ? 'rgba(90,126,245,0.08)' : '#13161e', transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ width: 18, height: 18, borderRadius: '50%', border: `2px solid ${active === p.id ? '#5a7ef5' : 'rgba(255,255,255,0.2)'}`, background: active === p.id ? '#5a7ef5' : 'transparent', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {active === p.id && <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#fff' }} />}
              </div>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: active === p.id ? '#5a7ef5' : '#e8eaef' }}>{p.label}</div>
                <div style={{ fontSize: 12, color: '#7b849a', marginTop: 3 }}>{p.desc}</div>
              </div>
            </div>
          ))}
          <C.Btn variant={saved ? 'success' : 'primary'} style={{ width: 'fit-content' }} onClick={handleSave}>{saved ? 'Saved' : 'Save Preset'}</C.Btn>
        </div>
      )}
    </div>
  );
}

function DemoPage() {
  const [status, setStatus] = React.useState('disconnected'); // disconnected, connecting, connected
  const [error, setError] = React.useState('');
  const [room, setRoom] = React.useState(null);

  const handleStart = async () => {
    setStatus('connecting');
    setError('');
    try {
      const res = await fetch('/api/demo/start', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to start demo');

      if (!window.LivekitClient) throw new Error("LiveKit Client not loaded. Please refresh.");

      const newRoom = new window.LivekitClient.Room({
        adaptiveStream: true,
        dynacast: true,
      });

      newRoom
        .on(window.LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
          if (track.kind === window.LivekitClient.Track.Kind.Audio) {
            const element = track.attach();
            document.body.appendChild(element);
          }
        })
        .on(window.LivekitClient.RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
          track.detach().forEach(el => el.remove());
        })
        .on(window.LivekitClient.RoomEvent.Disconnected, () => {
          setStatus('disconnected');
          setRoom(null);
        });

      await newRoom.connect(data.url, data.token);
      
      await newRoom.localParticipant.setMicrophoneEnabled(true);

      setRoom(newRoom);
      setStatus('connected');
    } catch (e) {
      setError(e.message);
      setStatus('disconnected');
    }
  };

  const handleStop = async () => {
    if (room) {
      await room.disconnect();
    }
    setStatus('disconnected');
    setRoom(null);
  };

  return (
    <div>
      <C.PageHeader title="Voice Agent Demo" sub="Talk directly to Aryan through your browser" />
      <div style={{ maxWidth: 560, display: 'grid', gap: 20 }}>
        <C.Card style={{ padding: 32, textAlign: 'center' }}>
          <div style={{ width: 80, height: 80, borderRadius: '50%', background: status === 'connected' ? 'rgba(52,211,153,0.1)' : 'rgba(90,126,245,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 24px', border: `2px solid ${status === 'connected' ? '#34d399' : '#5a7ef5'}` }}>
             <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke={status === 'connected' ? '#34d399' : '#5a7ef5'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="22"></line></svg>
          </div>
          
          <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 8, color: '#e8eaef' }}>
            {status === 'connected' ? 'Conversation Active' : 'Ready to Talk?'}
          </h2>
          <p style={{ fontSize: 14, color: '#7b849a', marginBottom: 32 }}>
            {status === 'connected' 
              ? 'Aryan is listening. Speak into your microphone.' 
              : 'Click the button below to start a live voice conversation with your Keevin IT Solutions agent.'}
          </p>

          {error && <div style={{ marginBottom: 20, color: '#f87171', fontSize: 13 }}>{error}</div>}

          {status === 'disconnected' ? (
            <C.Btn variant="primary" style={{ width: '100%', padding: '12px', fontSize: 15 }} onClick={handleStart}>
              Start Conversation
            </C.Btn>
          ) : status === 'connecting' ? (
            <C.Btn variant="primary" style={{ width: '100%', padding: '12px', fontSize: 15, opacity: 0.7 }} disabled>
              Connecting...
            </C.Btn>
          ) : (
            <C.Btn variant="ghost" style={{ width: '100%', padding: '12px', fontSize: 15, color: '#f87171', border: '1px solid rgba(248,113,113,0.3)' }} onClick={handleStop}>
              End Conversation
            </C.Btn>
          )}
        </C.Card>
      </div>
    </div>
  );
}

Object.assign(window, { OutboundPage, LanguagePage, DemoPage });
