import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import api from '../api'

/** 绝对路径 → 可播放 URL（成片在 /audio，分句在 /api/podcast/segments） */
export function toAudioUrl(path) {
  if (!path) return ''
  const parts = String(path).split('/').filter(Boolean)
  const name = parts[parts.length - 1]
  const epPart = parts.find((p) => p.startsWith('ep_'))
  if (epPart && name.endsWith('.wav')) {
    const epId = epPart.slice(3)
    return `/api/podcast/segments/${epId}/${name}`
  }
  return `/audio/${name}`
}

const draftKey = (projectId) => `aps:draft:v1:${projectId}`

export const useEditorStore = defineStore('editor', () => {
  const mode = ref('single')
  const script = ref('')
  const globalInstruction = ref('')

  const defaultHostA = () => ({
    model_type: 'builtin',
    voice_id: '冰糖',
    reference_audio: null,
    voice_description: '',
    style: '温柔',
    speed: '正常',
    emotion: '',
    audio_tag_style: '',
    speaker_names: [],
  })

  const defaultHostB = () => ({
    model_type: 'builtin',
    voice_id: '白桦',
    reference_audio: null,
    voice_description: '',
    style: '磁性低沉',
    speed: '偏慢',
    emotion: '',
    audio_tag_style: '',
    speaker_names: [],
  })

  const hostA = reactive(defaultHostA())
  const hostB = reactive(defaultHostB())
  const lastBuiltinVoice = reactive({ A: '冰糖', B: '白桦' })

  const audioPath = ref(null)
  const isPlaying = ref(false)
  const isSynthesizing = ref(false)
  const progressCurrent = ref(0)
  const progressTotal = ref(0)

  const parsedSpeakers = ref([])
  const parsedLines = ref([])
  /** parse-script 返回的超限信息，供合成前预检 */
  const scriptOverLimit = ref(false)
  const scriptMaxLines = ref(200)
  const scriptTotalLines = ref(0)

  const segments = ref([])
  /**
   * 上一次合成的失败分句快照（仅 error 句）。
   * 单独存一份的原因：脚本一改就会 clearSegmentStatus() 清空 segments，
   * 而「仅重试失败句」入口依赖失败清单，导致用户改个错别字就丢失重试上下文。
   * 失败清单属于「上一次合成」的结果，与当前编辑内容无关，因此不能被连带清掉。
   */
  const lastFailedSegments = ref([])
  /** 失败/停止清单所属的 episode，避免切到别的版本后误重试 */
  const lastResumableEpisodeId = ref(null)
  /** 停止后仍有 pending 句（无 error），用于展示「续跑」而不是「仅重试失败句」 */
  const lastStoppedPending = ref(false)
  const draftSavedAt = ref(null)
  const projectId = ref(null)
  const introText = ref('')
  const outroText = ref('')
  let draftTimer = null
  /** 草稿被显式清空（例如合成成功）后置位；避免组件卸载时又被 persistDraftNow 写回 */
  const draftCleared = ref(false)

  /** 会话代际：切换项目/重置后递增，用于丢弃过期异步结果 */
  const sessionEpoch = ref(0)
  let parseSeq = 0
  let previewSeq = 0

  const previewingKey = ref(null)
  const previewLoading = ref(false)
  const previewUrl = ref('')

  // ── TTS 供应商（全局设置，演播设定里快捷切换）──
  const ttsProvider = ref('mimo')
  const ttsProviders = ref([])
  const ttsCapabilities = ref({ builtin: true, design: true, clone: true })
  const ttsApiKeySet = ref(false)
  const ttsBuiltinVoices = ref([])
  const ttsDefaultVoices = ref({ A: null, B: null })
  /** 供应商变化时递增；VoicePanel 据此重拉音色/能力 */
  const ttsVoiceEpoch = ref(0)

  function bumpEpoch() {
    sessionEpoch.value += 1
    parseSeq += 1
    previewSeq += 1
  }

  function coerceHostsForTts() {
    const caps = ttsCapabilities.value
    const voices = ttsBuiltinVoices.value || []
    const ids = new Set(voices.map((v) => v.id))
    const fix = (host, channel) => {
      if (!host) return
      if (!caps[host.model_type || 'builtin']) {
        host.model_type = 'builtin'
        host.voice_description = ''
        host.reference_audio = null
      }
      if (host.model_type === 'builtin') {
        const fallback =
          ttsDefaultVoices.value?.[channel] || voices[0]?.id || null
        if (!host.voice_id || !ids.has(host.voice_id)) {
          host.voice_id = fallback
          if (fallback) rememberBuiltinVoice(channel, fallback)
        }
      }
    }
    fix(hostA, 'A')
    fix(hostB, 'B')
  }

  async function loadTtsContext() {
    const [settingsRes, voicesRes, metaRes] = await Promise.all([
      api.get('/api/settings'),
      api.get('/api/voices/builtin'),
      api.get('/api/voices/meta'),
    ])
    const settings = settingsRes.data || {}
    ttsProvider.value = settings.tts_provider || 'mimo'
    ttsProviders.value = settings.providers?.tts || []
    ttsApiKeySet.value = !!settings.api_key_set
    ttsBuiltinVoices.value = voicesRes.data?.voices || []
    ttsDefaultVoices.value = voicesRes.data?.defaults || { A: null, B: null }
    ttsCapabilities.value = metaRes.data?.capabilities || {
      builtin: true,
      design: true,
      clone: true,
    }
    return settings
  }

  async function switchTtsProvider(nextId) {
    const next = String(nextId || '').trim()
    if (!next || next === ttsProvider.value) return false
    await api.put('/api/settings', { tts_provider: next })
    ttsProvider.value = next
    await loadTtsContext()
    coerceHostsForTts()
    ttsVoiceEpoch.value += 1
    return true
  }

  function rememberBuiltinVoice(channel, voiceId) {
    if (!voiceId) return
    lastBuiltinVoice[channel === 'B' ? 'B' : 'A'] = voiceId
  }

  function applyHostConfig(channel, raw) {
    const target = channel === 'B' ? hostB : hostA
    const fallback = channel === 'B' ? defaultHostB() : defaultHostA()
    const data = { ...fallback, ...(raw || {}) }
    Object.keys(data).forEach((k) => {
      if (data[k] === undefined) data[k] = fallback[k] ?? null
    })
    if (!data.speaker_names) data.speaker_names = []
    if (!data.model_type) data.model_type = 'builtin'
    if (data.model_type === 'builtin' && !data.voice_id) {
      data.voice_id = lastBuiltinVoice[channel === 'B' ? 'B' : 'A'] || fallback.voice_id
    }
    Object.assign(target, data)
    if (data.model_type === 'builtin' && data.voice_id) {
      rememberBuiltinVoice(channel, data.voice_id)
    }
  }

  function switchModelType(channel, nextType) {
    const target = channel === 'B' ? hostB : hostA
    const fallbackVoice =
      lastBuiltinVoice[channel === 'B' ? 'B' : 'A'] || (channel === 'B' ? '白桦' : '冰糖')
    target.model_type = nextType
    target.reference_audio = null
    target.voice_description = ''
    target.voice_id = nextType === 'builtin' ? fallbackVoice : null
  }

  function normalizeConfig(config, channel = 'A') {
    const cleaned = { ...config }
    if (cleaned.reference_audio && typeof cleaned.reference_audio !== 'string') {
      cleaned.reference_audio = null
    }
    cleaned.audio_tag_style = cleaned.audio_tag_style || null
    cleaned.voice_description = cleaned.voice_description || null
    cleaned.emotion = cleaned.emotion || null
    cleaned.style = cleaned.style || null
    cleaned.speed = cleaned.speed || null
    cleaned.speaker_names = Array.isArray(cleaned.speaker_names)
      ? cleaned.speaker_names.filter(Boolean)
      : []

    const type = cleaned.model_type || 'builtin'
    const ch = channel === 'B' ? 'B' : 'A'
    if (type === 'builtin') {
      cleaned.reference_audio = null
      cleaned.voice_description = null
      cleaned.voice_id =
        cleaned.voice_id || lastBuiltinVoice[ch] || (ch === 'B' ? '白桦' : '冰糖')
    } else if (type === 'design') {
      cleaned.reference_audio = null
      cleaned.voice_id = null
    } else if (type === 'clone') {
      cleaned.voice_description = null
      cleaned.voice_id = null
    }
    return cleaned
  }

  function pruneSpeakerNames() {
    const alive = new Set(parsedSpeakers.value)
    const prune = (host) => {
      if (!Array.isArray(host.speaker_names) || host.speaker_names.length === 0) return
      const next = host.speaker_names.filter((n) => alive.has(n))
      if (next.length !== host.speaker_names.length) host.speaker_names = next
    }
    prune(hostA)
    prune(hostB)
  }

  function ensureDefaultSpeakerMapping() {
    const speakers = parsedSpeakers.value
    if (!speakers.length) return
    if (hostA.speaker_names?.length || hostB.speaker_names?.length) return
    hostA.speaker_names = [speakers[0]]
    if (speakers[1]) hostB.speaker_names = [speakers[1]]
  }

  function setMode(next) {
    mode.value = next
    if (next === 'dialogue') ensureDefaultSpeakerMapping()
  }

  function pickHostForSpeaker(speaker) {
    if (mode.value !== 'dialogue') return hostA
    if (hostA.speaker_names?.includes(speaker)) return hostA
    if (hostB.speaker_names?.includes(speaker)) return hostB
    if (!hostA.speaker_names?.length && !hostB.speaker_names?.length) return hostA
    return speaker ? hostB : hostA
  }

  // ── 草稿 ──────────────────────────────────────────────
  /** projectMode 以项目为准，草稿不得覆盖 */
  function loadDraft(pid, projectMode) {
    try {
      const raw = localStorage.getItem(draftKey(pid))
      if (!raw) return false
      const data = JSON.parse(raw)
      script.value = data.script || ''
      globalInstruction.value = data.globalInstruction || ''
      if (data.hostA) applyHostConfig('A', data.hostA)
      if (data.hostB) applyHostConfig('B', data.hostB)
      if (projectMode === 'single' || projectMode === 'dialogue') {
        mode.value = projectMode
      } else if (data.mode === 'single' || data.mode === 'dialogue') {
        mode.value = data.mode
      }
      introText.value = data.introText || ''
      outroText.value = data.outroText || ''
      draftSavedAt.value = data.savedAt || null
      return true
    } catch {
      return false
    }
  }

  function persistDraftNow() {
    if (!projectId.value) return
    // 已被显式清空、且此后没有新的编辑，就不要再把草稿「复活」
    if (draftCleared.value) return
    const payload = {
      script: script.value,
      globalInstruction: globalInstruction.value,
      hostA: { ...hostA },
      hostB: { ...hostB },
      mode: mode.value,
      introText: introText.value,
      outroText: outroText.value,
      savedAt: Date.now(),
    }
    try {
      localStorage.setItem(draftKey(projectId.value), JSON.stringify(payload))
      draftSavedAt.value = payload.savedAt
    } catch (e) {
      console.warn('草稿保存失败', e)
    }
  }

  function scheduleDraftSave() {
    if (!projectId.value) return
    // 有新的编辑，就重新允许写回
    draftCleared.value = false
    if (draftTimer) clearTimeout(draftTimer)
    draftTimer = setTimeout(persistDraftNow, 600)
  }

  function clearDraft(pid) {
    const id = pid || projectId.value
    if (!id) return
    // 必须同时取消挂起的定时写入，否则 600ms 后会把刚清掉的草稿又写回来
    if (draftTimer) {
      clearTimeout(draftTimer)
      draftTimer = null
    }
    localStorage.removeItem(draftKey(id))
    draftSavedAt.value = null
    draftCleared.value = true
  }

  function bindProject(pid) {
    projectId.value = pid
  }

  /** 脚本实质变更后清掉旧合成状态，避免 status 错位到新行 */
  function clearSegmentStatus() {
    segments.value = []
    // 刻意不动 lastFailedSegments：失败清单是上一次合成的结果，
    // 清掉会让「仅重试失败句」入口在用户改稿后凭空消失。
  }

  /**
   * 根据轮询结果更新续跑上下文。
   * - error：保留失败句清单
   * - cancelled：可能只有 pending（无 error），仍要能续跑
   * - done：清空
   */
  function noteResumeContext(episodeId, status, segs) {
    if (!episodeId) return
    const list = Array.isArray(segs) ? segs : []
    if (status === 'done') {
      lastFailedSegments.value = []
      lastResumableEpisodeId.value = null
      lastStoppedPending.value = false
      return
    }
    if (status !== 'error' && status !== 'cancelled') return
    const failed = list.filter((s) => s.status === 'error')
    const pendingBody = list.filter(
      (s) => typeof s.index === 'number' && s.status !== 'done'
    )
    lastFailedSegments.value = failed
    lastResumableEpisodeId.value = episodeId
    lastStoppedPending.value = status === 'cancelled' && pendingBody.length > 0
  }

  /** 仅当上下文属于该 episode（或强制清空）时移除续跑入口 */
  function clearResumeContext(episodeId) {
    if (episodeId == null || lastResumableEpisodeId.value === episodeId) {
      lastFailedSegments.value = []
      lastResumableEpisodeId.value = null
      lastStoppedPending.value = false
    }
  }

  // ── 脚本解析 ──────────────────────────────────────────
  async function parseScript() {
    if (!script.value.trim()) {
      parsedSpeakers.value = []
      parsedLines.value = []
      return
    }
    const seq = ++parseSeq
    const epoch = sessionEpoch.value
    try {
      const res = await api.post('/api/podcast/parse-script', { script: script.value })
      if (seq !== parseSeq || epoch !== sessionEpoch.value) return
      parsedSpeakers.value = res.data.speakers
      parsedLines.value = res.data.dialogue
      scriptOverLimit.value = !!res.data.over_limit
      scriptMaxLines.value = res.data.max_lines ?? scriptMaxLines.value
      scriptTotalLines.value = res.data.total_lines ?? 0
      pruneSpeakerNames()
      if (mode.value === 'dialogue') ensureDefaultSpeakerMapping()
    } catch (e) {
      if (seq === parseSeq) console.error('解析脚本失败:', e)
    }
  }

  // ── 单句试听 ──────────────────────────────────────────
  async function previewLine(line, key) {
    const speaker = line.speaker || ''
    const host = pickHostForSpeaker(speaker)
    const payload = normalizeConfig(host, host === hostB ? 'B' : 'A')
    const seq = ++previewSeq
    previewLoading.value = true
    previewingKey.value = key ?? `${speaker}:${line.text?.slice(0, 12)}`
    try {
      const res = await api.post('/api/podcast/preview-sentence', {
        text: line.text,
        model_type: payload.model_type,
        voice_id: payload.voice_id,
        reference_audio: payload.reference_audio,
        voice_description: payload.voice_description,
        style: payload.style,
        speed: payload.speed,
        emotion: payload.emotion,
        global_instruction: globalInstruction.value || null,
        audio_tag_style: payload.audio_tag_style,
      })
      if (seq !== previewSeq) return previewUrl.value
      previewUrl.value = toAudioUrl(res.data.audio_path)
      return previewUrl.value
    } finally {
      if (seq === previewSeq) previewLoading.value = false
    }
  }

  async function previewCurrentHost(channel = 'A') {
    const host = channel === 'B' ? hostB : hostA
    const sample = '你好，欢迎收听今天的节目。我们先从一个有趣的问题开始。'
    const payload = normalizeConfig(host, channel)
    const seq = ++previewSeq
    previewLoading.value = true
    previewingKey.value = `host-${channel}`
    try {
      const res = await api.post('/api/podcast/preview-sentence', {
        text: sample,
        model_type: payload.model_type,
        voice_id: payload.voice_id,
        reference_audio: payload.reference_audio,
        voice_description: payload.voice_description,
        style: payload.style,
        speed: payload.speed,
        emotion: payload.emotion,
        global_instruction: globalInstruction.value || null,
        audio_tag_style: payload.audio_tag_style,
      })
      if (seq !== previewSeq) return previewUrl.value
      previewUrl.value = toAudioUrl(res.data.audio_path)
      return previewUrl.value
    } finally {
      if (seq === previewSeq) previewLoading.value = false
    }
  }

  function stopPreview() {
    previewingKey.value = null
    previewUrl.value = ''
  }

  // ── 合成 / 重试 ───────────────────────────────────────
  async function pollStatus(
    episodeId,
    { intervalMs = 1500, timeoutMs = 30 * 60 * 1000, epoch = sessionEpoch.value } = {}
  ) {
    const started = Date.now()
    while (Date.now() - started < timeoutMs) {
      if (epoch !== sessionEpoch.value) {
        const err = new Error('会话已切换，停止轮询')
        err.cancelled = true
        throw err
      }
      const res = await api.get(`/api/podcast/episodes/${episodeId}/status`)
      if (epoch !== sessionEpoch.value) {
        const err = new Error('会话已切换，停止轮询')
        err.cancelled = true
        throw err
      }
      const data = res.data
      progressCurrent.value = data.progress_current || 0
      progressTotal.value = data.progress_total || 0
      segments.value = data.segments || []
      if (data.status === 'done') {
        audioPath.value = data.audio_path
        return data
      }
      if (data.status === 'cancelled') {
        // 用户主动停止：不是错误，但要中断轮询并让调用方复位界面
        const err = new Error('已停止合成，已完成的分句会保留')
        err.stopped = true
        err.segments = data.segments || []
        throw err
      }
      if (data.status === 'error') {
        const err = new Error(data.error_message || '合成失败')
        err.segments = data.segments || []
        err.failedCount = data.failed_count || 0
        throw err
      }
      await new Promise((r) => setTimeout(r, intervalMs))
    }
    throw new Error('合成超时，可在版本列表查看进度')
  }

  async function synthesize(episodeId) {
    const epoch = sessionEpoch.value
    isSynthesizing.value = true
    progressCurrent.value = 0
    progressTotal.value = 0
    segments.value = []
    try {
      const startRes = await api.post('/api/podcast/synthesize', { episode_id: episodeId })
      if (epoch !== sessionEpoch.value) return null
      progressTotal.value = startRes.data.total_lines || 0
      const data = await pollStatus(episodeId, { epoch })
      noteResumeContext(episodeId, 'done', data.segments)
      return data
    } catch (e) {
      // 有 segments 说明是轮询拿到的终态；HTTP 错误（409/400/网络）不要清掉上次续跑上下文
      if (e?.segments) {
        noteResumeContext(episodeId, e.stopped ? 'cancelled' : 'error', e.segments)
      }
      throw e
    } finally {
      if (epoch === sessionEpoch.value) isSynthesizing.value = false
    }
  }

  async function retrySynthesis(episodeId) {
    const epoch = sessionEpoch.value
    isSynthesizing.value = true
    progressCurrent.value = 0
    try {
      await api.post(`/api/podcast/episodes/${episodeId}/retry`)
      if (epoch !== sessionEpoch.value) return null
      const data = await pollStatus(episodeId, { epoch })
      noteResumeContext(episodeId, 'done', data.segments)
      return data
    } catch (e) {
      if (e?.segments) {
        noteResumeContext(episodeId, e.stopped ? 'cancelled' : 'error', e.segments)
      }
      throw e
    } finally {
      if (epoch === sessionEpoch.value) isSynthesizing.value = false
    }
  }

  /**
   * 请求后端中止合成任务。
   * 不是只停前端轮询 —— 后端任务会真的被 cancel，不再继续消耗 API 额度。
   * 返回后端是否确实中断了一个运行中的任务。
   */
  async function stopSynthesis(episodeId) {
    if (!episodeId) return false
    try {
      const res = await api.post(`/api/podcast/episodes/${episodeId}/cancel`)
      return !!res.data.cancelled
    } catch (e) {
      console.error('停止合成失败', e)
      return false
    }
  }

  async function estimateSynthesis(episodeId) {
    const res = await api.post(`/api/podcast/episodes/${episodeId}/estimate`)
    return res.data
  }

  function reset() {
    bumpEpoch()
    mode.value = 'single'
    script.value = ''
    globalInstruction.value = ''
    Object.assign(hostA, defaultHostA())
    Object.assign(hostB, defaultHostB())
    lastBuiltinVoice.A = '冰糖'
    lastBuiltinVoice.B = '白桦'
    audioPath.value = null
    progressCurrent.value = 0
    progressTotal.value = 0
    parsedSpeakers.value = []
    parsedLines.value = []
    scriptOverLimit.value = false
    scriptTotalLines.value = 0
    segments.value = []
    lastFailedSegments.value = []
    lastResumableEpisodeId.value = null
    lastStoppedPending.value = false
    draftCleared.value = false
    draftSavedAt.value = null
    introText.value = ''
    outroText.value = ''
    previewingKey.value = null
    previewUrl.value = ''
    isSynthesizing.value = false
    if (draftTimer) {
      clearTimeout(draftTimer)
      draftTimer = null
    }
  }

  return {
    mode, script, globalInstruction, hostA, hostB, lastBuiltinVoice,
    audioPath, isPlaying, isSynthesizing,
    progressCurrent, progressTotal,
    parsedSpeakers, parsedLines, segments, lastFailedSegments,
    scriptOverLimit, scriptMaxLines, scriptTotalLines,
    lastResumableEpisodeId, lastStoppedPending,
    draftSavedAt, draftCleared, projectId, introText, outroText, sessionEpoch,
    previewingKey, previewLoading, previewUrl,
    ttsProvider, ttsProviders, ttsCapabilities, ttsApiKeySet,
    ttsBuiltinVoices, ttsDefaultVoices, ttsVoiceEpoch,
    loadTtsContext, switchTtsProvider, coerceHostsForTts,
    parseScript, synthesize, retrySynthesis, stopSynthesis, estimateSynthesis, reset, normalizeConfig,
    switchModelType, applyHostConfig, setMode, pruneSpeakerNames, ensureDefaultSpeakerMapping,
    rememberBuiltinVoice, pickHostForSpeaker, clearSegmentStatus,
    noteResumeContext, clearResumeContext,
    loadDraft, scheduleDraftSave, persistDraftNow, clearDraft, bindProject,
    previewLine, previewCurrentHost, stopPreview, toAudioUrl,
  }
})
