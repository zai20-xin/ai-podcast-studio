<template>
  <div class="director-card">
    <div class="dir-head">
      <p class="studio-label">Direction</p>
      <h3>演播设定</h3>
      <p class="dir-sub">① 选 TTS 引擎与场景 → ② 再在下方选主播音色</p>
    </div>

    <div class="dir-grid">
      <div class="dir-field">
        <p class="field-label">
          TTS 引擎
          <span class="field-tag" :class="{ free: !activeTts?.requires_key }">
            {{ activeTts?.requires_key === false ? '免 Key' : '需 Key' }}
          </span>
        </p>
        <el-select
          v-model="selectedProvider"
          class="w-full"
          :loading="switching"
          placeholder="选择语音合成引擎"
          @change="onProviderChange"
        >
          <el-option
            v-for="p in store.ttsProviders"
            :key="p.id"
            :label="providerLabel(p)"
            :value="p.id"
          />
        </el-select>
        <p v-if="activeTts?.description" class="field-hint">{{ activeTts.description }}</p>
        <p v-if="capabilityLine" class="field-hint">{{ capabilityLine }}</p>
        <p v-if="switchHint" class="field-hint warn">{{ switchHint }}</p>
      </div>

      <div class="dir-field">
        <p class="field-label">节目场景</p>
        <el-select
          v-model="selectedScene"
          class="w-full"
          clearable
          filterable
          placeholder="选一个场景，自动填好导向与语速"
          @change="onSceneChange"
        >
          <el-option
            v-for="(preset, name) in scenePresets"
            :key="name"
            :label="name"
            :value="name"
          >
            <span class="opt-name">{{ name }}</span>
            <span class="opt-desc">{{ preset.description }}</span>
          </el-option>
        </el-select>
        <p v-if="activePreset" class="field-hint">{{ activePreset.description }}</p>
        <p v-else class="field-hint">可选。选了会写入下方「演出导向」，你仍可改文字</p>
        <p v-if="modeMismatch" class="field-hint warn">{{ modeMismatch }}</p>
      </div>

      <div class="dir-field grow">
        <p class="field-label">
          演出导向
          <span class="field-tag">发给每一句</span>
        </p>
        <el-input
          :model-value="globalInstruction"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="例：像朋友间聊天，口语化，少播音腔，关键处自然停顿"
          @update:model-value="$emit('update:globalInstruction', $event)"
        />
        <p class="field-hint">
          描述「怎么演」：角色、场景、语气。语速请用场景或右侧「微调语气」，不要写在这里。
          设计模式下会压成一句「说话方式」附在音色描述后，避免改声线。
          <template v-if="!store.ttsCapabilities.design && !store.ttsCapabilities.clone">
            当前引擎仅支持内置音色，「设计 / 克隆」不可用。
          </template>
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useEditorStore } from '../stores/editor'

const props = defineProps({
  globalInstruction: { type: String, default: '' },
  /** 用于把场景推荐音色/语速写回主播（仅 builtin 通道） */
  hostA: { type: Object, required: true },
  hostB: { type: Object, default: null },
  /** 项目模式：single | dialogue，用于格式场景与模式是否匹配的提示 */
  mode: { type: String, default: 'single' },
})

const emit = defineEmits(['update:globalInstruction'])

const store = useEditorStore()
const scenePresets = ref({})
const selectedScene = ref(null)
const selectedProvider = ref('mimo')
const switching = ref(false)

const activePreset = computed(() =>
  selectedScene.value ? scenePresets.value[selectedScene.value] : null
)

const activeTts = computed(() =>
  store.ttsProviders.find((p) => p.id === selectedProvider.value) ||
  store.ttsProviders.find((p) => p.id === store.ttsProvider) ||
  null
)

const capabilityLine = computed(() => {
  const caps = store.ttsCapabilities || {}
  const parts = []
  if (caps.builtin !== false) parts.push('内置音色')
  if (caps.design) parts.push('声音设计')
  if (caps.clone) parts.push('克隆')
  return parts.length ? `可用能力：${parts.join(' · ')}` : ''
})

const switchHint = computed(() => {
  if (activeTts.value?.requires_key === false) return null
  if (!store.ttsApiKeySet) {
    return '尚未配置该引擎的 API Key，请到「设置」填写，或先用 Edge TTS。'
  }
  return null
})

const modeMismatch = computed(() => {
  const name = selectedScene.value || ''
  if (!name) return null
  if (name.includes('双人') && props.mode !== 'dialogue') {
    return '当前是单人模式。双人对谈请新建项目时选「双人对谈」。'
  }
  if (name.includes('单人') && props.mode === 'dialogue') {
    return '当前是双人模式。若脚本只有旁白也可用；要一来一回请选「双人对谈」。'
  }
  return null
})

function providerLabel(p) {
  const free = p.requires_key === false ? ' · 免费' : ''
  const exp = p.status === 'supported' ? '' : ' · 实验性'
  return `${p.name}${free}${exp}`
}

watch(
  () => store.ttsProvider,
  (id) => {
    if (id && id !== selectedProvider.value) selectedProvider.value = id
  },
  { immediate: true }
)

onMounted(async () => {
  try {
    // 总是拉一次：设置页可能刚改过供应商，避免演播面板状态漂移
    await store.loadTtsContext()
    selectedProvider.value = store.ttsProvider
  } catch {
    /* 设置加载失败不阻塞编辑；可在系统设置页重试 */
  }
  try {
    const res = await api.get('/api/voices/presets')
    scenePresets.value = res.data.presets || {}
  } catch {
    /* 预设加载失败不阻塞编辑 */
  }
})

async function onProviderChange(id) {
  if (!id || id === store.ttsProvider) return
  const next = store.ttsProviders.find((p) => p.id === id)
  const needsKey = next?.requires_key !== false
  if (needsKey && !store.ttsApiKeySet) {
    ElMessage.warning('该引擎需要 API Key，请先在「设置」中配置')
    selectedProvider.value = store.ttsProvider
    return
  }

  try {
    await ElMessageBox.confirm(
      '切换 TTS 引擎后，主播音色会自动对齐到新引擎；已合成的分句将因指纹变化全部重做。',
      '切换 TTS 引擎',
      {
        type: 'warning',
        confirmButtonText: '切换',
        cancelButtonText: '取消',
      }
    )
  } catch {
    selectedProvider.value = store.ttsProvider
    return
  }

  switching.value = true
  try {
    await store.switchTtsProvider(id)
    ElMessage.success(`已切换到 ${next?.name || id}`)
  } catch (e) {
    selectedProvider.value = store.ttsProvider
    ElMessage.error(e.response?.data?.detail || e.message || '切换失败')
  } finally {
    switching.value = false
  }
}

function applySpeed(host, speed) {
  if (!host) return
  host.speed = speed || null
}

function onSceneChange(name) {
  if (!name) return
  const preset = scenePresets.value[name]
  if (!preset) return

  // 导向：场景写入后用户可继续改；换场景会覆盖，这是预期行为
  emit('update:globalInstruction', preset.global_instruction || '')

  // 语速：整档节目统一节奏
  applySpeed(props.hostA, preset.speed)
  applySpeed(props.hostB, preset.speed)

  // 推荐音色：只在 A 仍是内置音色时套用，避免覆盖用户选好的克隆/设计
  if (props.hostA && props.hostA.model_type === 'builtin' && preset.voice_id) {
    // Edge 等引擎音色表与 MiMo 不同，不在当前列表时不套用
    const ids = new Set((store.ttsBuiltinVoices || []).map((v) => v.id))
    if (!ids.size || ids.has(preset.voice_id)) {
      props.hostA.voice_id = preset.voice_id
    }
  }
}
</script>

<style scoped>
.director-card {
  background: var(--studio-panel);
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius);
  padding: 16px 18px;
  margin-bottom: 14px;
}

.dir-head h3 {
  margin: 2px 0 4px;
  font-size: 15px;
  font-weight: 600;
}

.dir-sub {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--studio-muted, #888);
}

.dir-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dir-field .w-full {
  width: 100%;
}

.field-label {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--studio-ink, #333);
  display: flex;
  align-items: center;
  gap: 8px;
}

.field-tag {
  font-weight: 400;
  font-size: 11px;
  color: var(--studio-muted, #888);
  border: 1px solid var(--studio-line);
  border-radius: 999px;
  padding: 0 8px;
}

.field-tag.free {
  color: var(--el-color-success, #67c23a);
  border-color: var(--el-color-success, #67c23a);
}

.field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--studio-muted, #888);
}

.field-hint.warn {
  color: var(--el-color-warning, #e6a23c);
}

.opt-name {
  float: left;
}

.opt-desc {
  float: right;
  font-size: 12px;
  color: var(--studio-muted, #999);
  max-width: 55%;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
