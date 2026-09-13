/**
 * 前端单元测试：editor store 纯逻辑
 * 通过 vi.mock 替换 api，不依赖真实后端
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

if (typeof globalThis.localStorage === 'undefined') {
  const map = new Map()
  globalThis.localStorage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  }
}

vi.mock('../src/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}))

import api from '../src/api'
import { useEditorStore, toAudioUrl } from '../src/stores/editor'

describe('toAudioUrl', () => {
  it('episode wav maps to /audio', () => {
    expect(toAudioUrl('/data/audio/episode_1.wav')).toMatch(/^\/audio\/episode_1\.wav$/)
  })

  it('segment under ep_N maps to segments API', () => {
    const url = toAudioUrl('/data/audio/ep_12/003.wav')
    expect(url).toBe('/api/podcast/segments/12/003.wav')
  })

  it('empty', () => {
    expect(toAudioUrl('')).toBe('')
  })
})

describe('editor store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('normalizeConfig strips mode-irrelevant fields for builtin', () => {
    const store = useEditorStore()
    const out = store.normalizeConfig(
      {
        model_type: 'builtin',
        voice_id: '茉莉',
        reference_audio: '/x.wav',
        voice_description: 'desc',
        audio_tag_style: '',
        style: '温柔',
        speed: null,
        emotion: '',
        speaker_names: ['A', ''],
      },
      'A'
    )
    expect(out.reference_audio).toBeNull()
    expect(out.voice_description).toBeNull()
    expect(out.voice_id).toBe('茉莉')
    expect(out.speaker_names).toEqual(['A'])
    expect(out.speed).toBeNull()
  })

  it('normalizeConfig uses channel B default voice', () => {
    const store = useEditorStore()
    const out = store.normalizeConfig({ model_type: 'builtin', voice_id: null }, 'B')
    expect(out.voice_id).toBe('白桦')
  })

  it('normalizeConfig design clears voice_id and reference', () => {
    const store = useEditorStore()
    const out = store.normalizeConfig(
      { model_type: 'design', voice_description: '年轻女声', voice_id: '冰糖', reference_audio: 'x' },
      'A'
    )
    expect(out.voice_id).toBeNull()
    expect(out.reference_audio).toBeNull()
    expect(out.voice_description).toBe('年轻女声')
  })

  it('pruneSpeakerNames removes dead speakers', () => {
    const store = useEditorStore()
    store.hostA.speaker_names = ['A', '已删除']
    store.parsedSpeakers = ['A']
    store.pruneSpeakerNames()
    expect(store.hostA.speaker_names).toEqual(['A'])
  })

  it('pickHostForSpeaker follows mapping', () => {
    const store = useEditorStore()
    store.setMode('dialogue')
    store.hostA.speaker_names = ['甲']
    store.hostB.speaker_names = ['乙']
    expect(store.pickHostForSpeaker('甲')).toBe(store.hostA)
    expect(store.pickHostForSpeaker('乙')).toBe(store.hostB)
  })

  it('loadDraft does not override project mode', () => {
    const store = useEditorStore()
    store.bindProject('99')
    store.persistDraftNow()
    // 手工写入冲突 mode 的草稿
    localStorage.setItem(
      'aps:draft:v1:99',
      JSON.stringify({ script: '草稿脚本', mode: 'single', savedAt: Date.now() })
    )
    const ok = store.loadDraft('99', 'dialogue')
    expect(ok).toBe(true)
    expect(store.mode).toBe('dialogue')
    expect(store.script).toBe('草稿脚本')
  })

  it('clearSegmentStatus empties segments', () => {
    const store = useEditorStore()
    store.segments = [{ index: 0, status: 'done' }]
    store.clearSegmentStatus()
    expect(store.segments).toEqual([])
  })

  it('parseScript ignores stale responses', async () => {
    const store = useEditorStore()
    store.script = 'A: 一\nB: 二'
    let resolveFirst
    api.post.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolveFirst = r
        })
    )
    api.post.mockImplementationOnce(() =>
      Promise.resolve({ data: { speakers: ['B'], total_lines: 1, dialogue: [{ speaker: 'B', text: '新' }] } })
    )
    const p1 = store.parseScript()
    const p2 = store.parseScript()
    resolveFirst({
      data: { speakers: ['旧'], total_lines: 1, dialogue: [{ speaker: '旧', text: '旧' }] },
    })
    await Promise.all([p1, p2])
    expect(store.parsedSpeakers).toEqual(['B'])
  })

  it('switchModelType restores last builtin voice', () => {
    const store = useEditorStore()
    store.rememberBuiltinVoice('A', '苏打')
    store.switchModelType('A', 'clone')
    expect(store.hostA.voice_id).toBeNull()
    store.switchModelType('A', 'builtin')
    expect(store.hostA.voice_id).toBe('苏打')
  })
})

describe('合成中断与失败上下文', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('clearSegmentStatus 保留失败清单，改稿后仍能续跑', () => {
    const store = useEditorStore()
    store.lastFailedSegments = [{ index: 2, status: 'error', error: '超时' }]
    store.segments = [{ index: 0, status: 'done' }]
    store.clearSegmentStatus()
    expect(store.segments).toEqual([])
    // 失败清单属于上一次合成的结果，不能被连带清掉，否则重试入口会消失
    expect(store.lastFailedSegments).toHaveLength(1)
  })

  it('轮询遇到 cancelled 会中断并标记 stopped', async () => {
    const store = useEditorStore()
    api.post.mockResolvedValueOnce({ data: { total_lines: 5 } })
    api.get.mockResolvedValueOnce({
      data: { status: 'cancelled', progress_current: 2, progress_total: 5, segments: [] },
    })
    await expect(store.synthesize(1)).rejects.toMatchObject({ stopped: true })
    expect(store.isSynthesizing).toBe(false)
  })

  it('clearDraft 会取消挂起的写入，草稿不复活', () => {
    vi.useFakeTimers()
    const store = useEditorStore()
    store.bindProject(7)
    store.script = 'A: 内容'
    store.scheduleDraftSave()
    store.clearDraft()
    vi.advanceTimersByTime(1000)
    expect(localStorage.getItem('aps:draft:v1:7')).toBeNull()
    vi.useRealTimers()
  })

  it('clearDraft 之后再次编辑，草稿恢复保存', () => {
    vi.useFakeTimers()
    const store = useEditorStore()
    store.bindProject(8)
    store.clearDraft()
    store.script = 'A: 新的内容'
    store.scheduleDraftSave()
    vi.advanceTimersByTime(1000)
    expect(localStorage.getItem('aps:draft:v1:8')).not.toBeNull()
    vi.useRealTimers()
  })

  it('合成失败时留存失败清单，成功时清空', async () => {
    const store = useEditorStore()
    api.post.mockResolvedValueOnce({ data: { total_lines: 3 } })
    api.get.mockResolvedValueOnce({
      data: {
        status: 'error',
        error_message: '第 2 句失败',
        segments: [
          { index: 0, status: 'done' },
          { index: 1, status: 'error', error: '超时' },
        ],
        failed_count: 1,
      },
    })
    await expect(store.synthesize(1)).rejects.toThrow()
    expect(store.lastFailedSegments).toHaveLength(1)
    expect(store.lastFailedSegments[0].index).toBe(1)
  })

  it('stopSynthesis 调用后端取消接口', async () => {
    const store = useEditorStore()
    api.post.mockResolvedValueOnce({ data: { cancelled: true } })
    await expect(store.stopSynthesis(9)).resolves.toBe(true)
    expect(api.post).toHaveBeenCalledWith('/api/podcast/episodes/9/cancel')
  })

  it('取消后仅 pending 也会标记可续跑，而失败清单可为空', async () => {
    const store = useEditorStore()
    api.post.mockResolvedValueOnce({ data: { total_lines: 3 } })
    api.get.mockResolvedValueOnce({
      data: {
        status: 'cancelled',
        segments: [
          { index: 0, status: 'done' },
          { index: 1, status: 'pending' },
          { index: 2, status: 'pending' },
        ],
      },
    })
    await expect(store.synthesize(11)).rejects.toMatchObject({ stopped: true })
    expect(store.lastFailedSegments).toHaveLength(0)
    expect(store.lastResumableEpisodeId).toBe(11)
    expect(store.lastStoppedPending).toBe(true)
  })

  it('HTTP 错误（无 segments）不清掉上次续跑上下文', async () => {
    const store = useEditorStore()
    store.lastFailedSegments = [{ index: 2, status: 'error', error: '旧失败' }]
    store.lastResumableEpisodeId = 77
    api.post.mockRejectedValueOnce({
      response: { status: 409, data: { detail: '该单集正在合成中' } },
      message: 'Request failed with status code 409',
    })
    await expect(store.synthesize(88)).rejects.toThrow()
    expect(store.lastFailedSegments).toHaveLength(1)
    expect(store.lastResumableEpisodeId).toBe(77)
  })

  it('clearResumeContext 只清指定 episode', () => {
    const store = useEditorStore()
    store.lastFailedSegments = [{ index: 0, status: 'error' }]
    store.lastResumableEpisodeId = 5
    store.clearResumeContext(9)
    expect(store.lastResumableEpisodeId).toBe(5)
    store.clearResumeContext(5)
    expect(store.lastResumableEpisodeId).toBeNull()
    expect(store.lastFailedSegments).toHaveLength(0)
  })

  it('parseScript 记录 over_limit', async () => {
    const store = useEditorStore()
    store.script = 'A: 超长脚本'
    api.post.mockResolvedValueOnce({
      data: {
        speakers: ['A'],
        total_lines: 220,
        max_lines: 200,
        over_limit: true,
        dialogue: [],
      },
    })
    await store.parseScript()
    expect(store.scriptOverLimit).toBe(true)
    expect(store.scriptMaxLines).toBe(200)
  })

  it('switchTtsProvider 写回设置、刷新上下文并纠正主播', async () => {
    const store = useEditorStore()
    store.hostA.model_type = 'clone'
    store.hostA.reference_audio = '/x.wav'
    store.hostA.voice_id = null
    store.hostB.model_type = 'builtin'
    store.hostB.voice_id = '白桦'

    api.get.mockImplementation((url) => {
      if (url === '/api/settings') {
        return Promise.resolve({
          data: {
            tts_provider: 'edge-tts',
            api_key_set: false,
            providers: {
              tts: [
                { id: 'edge-tts', name: 'Edge TTS（免费）', requires_key: false, status: 'supported' },
                { id: 'mimo', name: 'Xiaomi MiMo TTS', requires_key: true, status: 'supported' },
              ],
            },
          },
        })
      }
      if (url === '/api/voices/builtin') {
        return Promise.resolve({
          data: {
            provider: 'edge-tts',
            voices: [
              { id: 'zh-CN-XiaoxiaoNeural', name: '晓晓' },
              { id: 'zh-CN-YunxiNeural', name: '云希' },
            ],
            defaults: { A: 'zh-CN-XiaoxiaoNeural', B: 'zh-CN-YunxiNeural' },
          },
        })
      }
      if (url === '/api/voices/meta') {
        return Promise.resolve({
          data: {
            tts_provider: 'edge-tts',
            capabilities: { builtin: true, design: false, clone: false },
          },
        })
      }
      return Promise.resolve({ data: {} })
    })
    api.put.mockResolvedValue({ data: { message: 'ok' } })

    const changed = await store.switchTtsProvider('edge-tts')
    expect(changed).toBe(true)
    expect(api.put).toHaveBeenCalledWith('/api/settings', { tts_provider: 'edge-tts' })
    expect(store.ttsProvider).toBe('edge-tts')
    expect(store.ttsCapabilities.clone).toBe(false)
    // clone 不可用 → 退回 builtin，并套用默认 Edge 音色
    expect(store.hostA.model_type).toBe('builtin')
    expect(store.hostA.voice_id).toBe('zh-CN-XiaoxiaoNeural')
    expect(store.hostB.voice_id).toBe('zh-CN-YunxiNeural')
    expect(store.ttsVoiceEpoch).toBe(1)
  })

  it('switchTtsProvider 同 id 为空操作', async () => {
    const store = useEditorStore()
    store.ttsProvider = 'mimo'
    const changed = await store.switchTtsProvider('mimo')
    expect(changed).toBe(false)
    expect(api.put).not.toHaveBeenCalled()
  })
})
