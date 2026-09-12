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
