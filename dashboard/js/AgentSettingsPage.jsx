// ─── Agent Settings Page ─────────────────────────────────────────────────────

const GEMINI_DEFAULT_MODEL = 'gemini-3.1-flash-native-audio-preview';
const GEMINI_TTS_DEFAULT_MODEL = 'gemini-3.1-flash-tts-preview';
const GEMINI_LANGUAGE_OPTIONS = [
  { value: '', label: 'Auto-detect' },
  { value: 'en-IN', label: 'English (India)' },
  { value: 'hi-IN', label: 'Hindi' },
  { value: 'mr-IN', label: 'Marathi' },
  { value: 'ta-IN', label: 'Tamil' },
  { value: 'te-IN', label: 'Telugu' },
];

const CFG_DEFAULTS = {
  first_line: "Namaste! This is Aryan from SPX AI. We help businesses automate with AI. Hmm, may I ask what kind of business you run?",
  agent_instructions: "",
  voice_mode: 'gemini_live',
  llm_provider: 'gemini',
  llm_model: GEMINI_DEFAULT_MODEL,
  gemini_live_model: GEMINI_DEFAULT_MODEL, 
  gemini_live_voice: 'Puck', 
  gemini_live_temperature: 0.8, 
  gemini_live_language: '', 
  gemini_tts_model: GEMINI_TTS_DEFAULT_MODEL,
  livekit_url: '', sip_trunk_id: '',
  livekit_api_key: '', livekit_api_secret: '',
  google_api_key: '',
};

function GeneralTab({ cfg, set }) {
  return (
    <div style={{ display: 'grid', gap: 20 }}>
      <C.Input label="Opening Greeting" value={cfg.first_line || ''} onChange={set('first_line')} rows={3}
        hint="The first sentence your agent speaks when a prospect answers." />
      <C.Input label="System Prompt" value={cfg.agent_instructions || ''} onChange={set('agent_instructions')} rows={10} mono
        hint="Full instructions that shape agent personality, goals, and knowledge rules." />
    </div>
  );
}

function ModelsTab({ cfg, set }) {
  return (
    <div style={{ display: 'grid', gap: 20 }}>
      <C.SectionTitle>Gemini 3.1 Live API</C.SectionTitle>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <C.Input
          label="Model"
          value={cfg.gemini_live_model || ''}
          onChange={set('gemini_live_model')}
          placeholder={GEMINI_DEFAULT_MODEL}
          hint="Default: Gemini 3.1 Live preview."
          mono
        />
        <C.Input label="Voice" value={cfg.gemini_live_voice || 'Puck'} onChange={set('gemini_live_voice')} />
        <C.Select
          label="Language"
          value={cfg.gemini_live_language ?? ''}
          onChange={set('gemini_live_language')}
          options={GEMINI_LANGUAGE_OPTIONS}
          hint="Leave on Auto-detect for multilingual campaigns."
        />
        <C.Input
          label="Scripted TTS Model"
          value={cfg.gemini_tts_model || GEMINI_TTS_DEFAULT_MODEL}
          onChange={set('gemini_tts_model')}
          placeholder={GEMINI_TTS_DEFAULT_MODEL}
          mono
        />
        <C.Input label="Temperature" type="number" value={cfg.gemini_live_temperature ?? 0.8} onChange={v => set('gemini_live_temperature')(parseFloat(v))} />
      </div>
    </div>
  );
}

function CredentialsTab({ cfg, set }) {
  return (
    <div style={{ display: 'grid', gap: 20 }}>
      <C.SectionTitle>LiveKit</C.SectionTitle>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <C.Input label="LiveKit URL" value={cfg.livekit_url || ''} onChange={set('livekit_url')} mono placeholder="wss://…" />
        <C.Input label="SIP Trunk ID" value={cfg.sip_trunk_id || ''} onChange={set('sip_trunk_id')} mono />
        <C.Input label="API Key" value={cfg.livekit_api_key || ''} onChange={set('livekit_api_key')} mono />
        <C.Input label="API Secret" value={cfg.livekit_api_secret || ''} onChange={set('livekit_api_secret')} mono />
      </div>
      <C.SectionTitle>AI Provider Secrets</C.SectionTitle>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <C.Input label="Google API Key" value={cfg.google_api_key || ''} onChange={set('google_api_key')} mono />
        <C.Input label="Deepgram API Key" value={cfg.deepgram_api_key || ''} onChange={set('deepgram_api_key')} mono />
        <C.Input label="ElevenLabs API Key" value={cfg.elevenlabs_api_key || ''} onChange={set('elevenlabs_api_key')} mono />
      </div>
    </div>
  );
}

function AgentSettingsPage() {
  const [tab, setTab] = React.useState('General');
  const [cfg, setCfg] = React.useState({ ...CFG_DEFAULTS });
  const [loading, setLoading] = React.useState(true);
  const [saveState, setSaveState] = React.useState('idle'); // idle | saving | saved | error
  const tabs = ['General', 'Models & Voice', 'API Credentials'];
  const set = k => v => setCfg(p => ({ ...p, [k]: v }));

  React.useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(data => { setCfg(p => ({ ...p, ...data })); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaveState('saving');
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      });
      if (!res.ok) throw new Error('Save failed');
      setSaveState('saved');
      setTimeout(() => setSaveState('idle'), 2500);
    } catch {
      setSaveState('error');
      setTimeout(() => setSaveState('idle'), 3000);
    }
  };

  const saveLabel = { idle: 'Save Changes', saving: 'Saving…', saved: '✓ Saved', error: '✗ Error' }[saveState];
  const saveVariant = saveState === 'saved' ? 'success' : saveState === 'error' ? 'danger' : 'primary';

  return (
    <div>
      <C.PageHeader title="Agent Settings"
        sub="Configure your voice agent's behaviour, models, and credentials"
        action={<C.Btn variant={saveVariant} onClick={handleSave} disabled={saveState === 'saving'}>{saveLabel}</C.Btn>} />
      <div style={{ marginBottom: 24 }}>
        <C.Tabs tabs={tabs} active={tab} onChange={setTab} />
      </div>
      {loading ? <C.Spinner /> : (
        <C.Card style={{ padding: 24 }}>
          {tab === 'General'         && <GeneralTab cfg={cfg} set={set} />}
          {tab === 'Models & Voice'  && <ModelsTab cfg={cfg} set={set} />}
          {tab === 'API Credentials' && <CredentialsTab cfg={cfg} set={set} />}
        </C.Card>
      )}
    </div>
  );
}

Object.assign(window, { AgentSettingsPage });
