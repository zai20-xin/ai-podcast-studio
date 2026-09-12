/**
 * 前后端契约测试（前端侧）
 * 读取 contracts/api-contract.json，校验前端 store / URL 约定与后端一致
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'

const here = path.dirname(fileURLToPath(import.meta.url))
const contractPath = path.resolve(here, '../../contracts/api-contract.json')
const contract = JSON.parse(readFileSync(contractPath, 'utf-8'))

import { useEditorStore, toAudioUrl } from '../src/stores/editor'

describe('API contract (frontend)', () => {
  it('contract file loads with expected sections', () => {
    expect(contract.host_config_fields.length).toBeGreaterThan(0)
    expect(contract.model_types).toEqual(['builtin', 'design', 'clone'])
    expect(Object.keys(contract.endpoints)).toContain('GET /api/settings')
  })

  it('normalizeConfig emits all host_config_fields keys', () => {
    setActivePinia(createPinia())
    const store = useEditorStore()
    const out = store.normalizeConfig(
      {
        model_type: 'builtin',
        voice_id: '冰糖',
        reference_audio: null,
        voice_description: null,
        style: '温柔',
        speed: '正常',
        emotion: null,
        audio_tag_style: null,
        speaker_names: [],
      },
      'A'
    )
    for (const field of contract.host_config_fields) {
      expect(out).toHaveProperty(field)
    }
    expect(contract.model_types).toContain(out.model_type)
  })

  it('toAudioUrl matches contract patterns', () => {
    const { episode_file_pattern, segment_file_pattern } = contract.frontend_audio_url
    const episodeUrl = toAudioUrl('/tmp/data/audio/episode_3.wav')
    const segUrl = toAudioUrl('/tmp/data/audio/ep_3/001.wav')
    expect(episodeUrl).toMatch(new RegExp(episode_file_pattern))
    expect(segUrl).toMatch(new RegExp(segment_file_pattern))
  })

  it('endpoint keys used by frontend exist in contract', () => {
    // 前端实际调用的关键路径（编辑/设置/音色）
    const required = [
      'GET /api/settings',
      'PUT /api/settings',
      'GET /api/voices/meta',
      'GET /api/voices/builtin',
      'GET /api/voices/presets',
      'POST /api/projects',
      'GET /api/projects',
      'POST /api/podcast/parse-script',
      'POST /api/podcast/episodes',
      'GET /api/podcast/episodes/{id}/status',
      'POST /api/podcast/episodes/{id}/estimate',
      'POST /api/script/rewrite',
    ]
    for (const ep of required) {
      expect(contract.endpoints[ep], `契约缺少 ${ep}`).toBeTruthy()
    }
  })

  it('settings response keys match Settings.vue usage', () => {
    const keys = contract.endpoints['GET /api/settings'].required_keys
    for (const k of [
      'api_key_set',
      'masked_key',
      'llm_api_key_set',
      'llm_base_url',
      'llm_model',
      'masked_llm_key',
    ]) {
      expect(keys).toContain(k)
    }
  })

  it('status response keys match editor poll usage', () => {
    const keys = contract.endpoints['GET /api/podcast/episodes/{id}/status'].required_keys
    for (const k of ['status', 'progress_current', 'progress_total', 'segments', 'error_message']) {
      expect(keys).toContain(k)
    }
  })
})
