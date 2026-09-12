<template>
  <div class="voice-panel">
    <div class="panel-header">
      <div class="ch-badge">{{ label }}</div>
    </div>

    <el-form label-position="top" size="default">
      <el-form-item label="快速场景">
        <el-select
          v-model="selectedPreset"
          style="width: 100%"
          placeholder="选择场景预设"
          clearable
          @change="applyPreset"
        >
          <el-option
            v-for="(preset, name) in scenePresets"
            :key="name"
            :label="name"
            :value="name"
          >
            <span>{{ name }}</span>
            <span class="opt-desc">{{ preset.description }}</span>
          </el-option>
        </el-select>
      </el-form-item>

      <el-form-item v-if="showSpeakerMapping" label="负责的角色">
        <el-select
          v-model="config.speaker_names"
          style="width: 100%"
          multiple
          clearable
          placeholder="选择该主播负责的角色"
        >
          <el-option
            v-for="speaker in availableSpeakers"
            :key="speaker"
            :label="speaker"
            :value="speaker"
          />
        </el-select>
        <p class="tip">
          <template v-if="availableSpeakers.length">
            已识别：{{ availableSpeakers.join('、') }}
          </template>
          <template v-else>
            在脚本中写「角色：台词」后会自动识别
          </template>
        </p>
      </el-form-item>

      <el-form-item label="声音来源">
        <el-radio-group v-model="config.model_type" @change="onModelTypeChange">
          <el-radio-button value="builtin">内置</el-radio-button>
          <el-radio-button value="design">设计</el-radio-button>
          <el-radio-button value="clone">克隆</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item v-if="config.model_type === 'builtin'" label="选择音色">
        <el-select
          v-model="config.voice_id"
          style="width: 100%"
          placeholder="请选择音色"
          @change="onBuiltinVoiceChange"
        >
          <el-option
            v-for="voice in builtinVoices"
            :key="voice.id"
            :label="`${voice.name} (${voice.gender})`"
            :value="voice.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item v-if="config.model_type === 'design'" label="声音描述">
        <el-input
          v-model="config.voice_description"
          type="textarea"
          :rows="3"
          maxlength="300"
          show-word-limit
          placeholder="例如：二十多岁女性，声音温暖柔和，像深夜电台主播"
        />
        <p class="tip">用文字描述生成声音，无需参考音频；切换来源时会清空</p>
      </el-form-item>

      <template v-if="config.model_type === 'clone'">
        <el-form-item label="已保存的克隆音色">
          <div class="clone-row">
            <el-select
              v-model="selectedClonedVoiceId"
              style="flex: 1"
              clearable
              placeholder="选择已上传的克隆音色"
              @change="onSelectClonedVoice"
            >
              <el-option
                v-for="v in clonedVoices"
                :key="v.id"
                :label="`${v.name} (#${v.id})`"
                :value="v.id"
              />
            </el-select>
            <el-button
              size="default"
              :disabled="!selectedClonedVoiceId"
              :loading="clonePreviewLoading"
              @click="playClonedRef"
            >
              试听
            </el-button>
            <el-button
              size="default"
              type="danger"
              plain
              :disabled="!selectedClonedVoiceId"
              @click="removeClonedVoice"
            >
              删除
            </el-button>
          </div>
          <audio v-if="clonePreviewUrl" ref="cloneAudio" :src="clonePreviewUrl" class="try-audio" controls />
        </el-form-item>

        <el-form-item label="上传参考音频">
          <el-upload
            ref="uploadRef"
            class="upload-area"
            drag
            :auto-upload="false"
            :show-file-list="false"
            :limit="1"
            accept=".wav,.mp3"
            @change="onAudioUpload"
            @exceed="onUploadExceed"
          >
            <div v-if="config.reference_audio" class="upload-preview ok">
              <el-icon><Check /></el-icon>
              <span>参考音频已就绪</span>
              <el-button size="small" text @click.stop="clearReferenceAudio">移除</el-button>
            </div>
            <div v-else-if="uploading" class="upload-preview">
              <el-icon class="is-loading"><Loading /></el-icon>
              <span>上传中…</span>
            </div>
            <div v-else class="upload-placeholder">
              <el-icon><Upload /></el-icon>
              <span>拖入或点击上传</span>
              <span class="tip">10–15 秒干净人声 · wav/mp3 · ≤10MB</span>
            </div>
          </el-upload>
        </el-form-item>
      </template>

      <div class="row-2">
        <el-form-item label="风格">
          <el-select v-model="config.style" style="width: 100%" clearable placeholder="默认风格">
            <el-option v-for="name in styleList" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item label="语速">
          <el-select v-model="config.speed" style="width: 100%" clearable placeholder="默认语速">
            <el-option v-for="name in speedList" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
      </div>

      <div class="row-2">
        <el-form-item label="句首标签">
          <el-select
            v-model="config.audio_tag_style"
            style="width: 100%"
            clearable
            placeholder="不添加"
          >
            <el-option v-for="name in tagList" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item label="情绪">
          <el-input v-model="config.emotion" placeholder="开心 / 沉稳" clearable />
        </el-form-item>
      </div>

      <el-button
        class="try-btn"
        size="small"
        :loading="trying"
        @click="tryCurrentVoice"
      >
        用当前配置试听示例句
      </el-button>
      <audio v-if="tryUrl" ref="tryAudio" :src="tryUrl" class="try-audio" controls />
    </el-form>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import { Check, Upload, Loading } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { useEditorStore } from '../stores/editor'

const editorStore = useEditorStore()
const trying = ref(false)
const tryUrl = ref('')
const tryAudio = ref(null)
const clonePreviewUrl = ref('')
const clonePreviewLoading = ref(false)
const cloneAudio = ref(null)

const props = defineProps({
  label: String,
  channel: { type: String, default: 'A' },
  config: Object,
  globalInstruction: String,
  availableSpeakers: { type: Array, default: () => [] },
  showSpeakerMapping: { type: Boolean, default: false },
  /** 仅 A 通道在全局指令为空时写入预设指令，避免双通道互相覆盖 */
  canWriteGlobal: { type: Boolean, default: true },
})

const emit = defineEmits(['update:globalInstruction'])

const builtinVoices = ref([])
const clonedVoices = ref([])
const scenePresets = ref({})
const selectedPreset = ref(null)
const selectedClonedVoiceId = ref(null)
const uploading = ref(false)
const uploadRef = ref(null)
const styleList = ref([])
const speedList = ref([])
const tagList = ref([])

onMounted(async () => {
  try {
    const [voicesRes, presetsRes, metaRes, clonedRes] = await Promise.all([
      api.get('/api/voices/builtin'),
      api.get('/api/voices/presets'),
      api.get('/api/voices/meta'),
      api.get('/api/voices/cloned'),
    ])
    builtinVoices.value = voicesRes.data.voices
    scenePresets.value = presetsRes.data.presets
    styleList.value = metaRes.data.styles || []
    speedList.value = metaRes.data.speeds || []
    tagList.value = metaRes.data.audio_tags || []
    clonedVoices.value = clonedRes.data || []
  } catch {
    ElMessage.error('加载音色配置失败')
  }
})

// 加载历史版本时同步 UI 选中态
watch(
  () => props.config,
  (cfg) => {
    if (!cfg) return
    if (cfg.model_type === 'clone' && cfg.reference_audio) {
      const hit = clonedVoices.value.find((v) => v.reference_path === cfg.reference_audio)
      selectedClonedVoiceId.value = hit ? hit.id : null
    } else {
      selectedClonedVoiceId.value = null
    }
    // 手动改过配置后，清掉场景预设高亮，避免“看似仍套着预设”
    if (cfg.model_type !== 'builtin' && selectedPreset.value) {
      selectedPreset.value = null
    }
  },
  { deep: true }
)

async function tryCurrentVoice() {
  trying.value = true
  try {
    tryUrl.value = await editorStore.previewCurrentHost(props.channel)
    await nextTick()
    tryAudio.value?.play()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '试听失败')
  } finally {
    trying.value = false
  }
}

function applyPreset(presetName) {
  if (!presetName) return
  const preset = scenePresets.value[presetName]
  if (!preset) return

  // 预设走内置音色；通过 switch 清掉 design/clone 残留
  if (props.config.model_type !== 'builtin') {
    props.config.model_type = 'builtin'
    props.config.reference_audio = null
    props.config.voice_description = ''
    selectedClonedVoiceId.value = null
  }
  props.config.voice_id = preset.voice_id
  props.config.style = preset.style || null
  props.config.speed = preset.speed || null
  props.config.emotion = preset.emotion || ''
  // 预设不改句首标签，避免静默覆盖用户细调
  if (preset.voice_id) {
    // 记住该通道音色
    emit('remember-voice', preset.voice_id)
  }

  // 仅在全局指令为空时写入，防止双通道预设互相覆盖
  if (props.canWriteGlobal && !(props.globalInstruction || '').trim()) {
    emit('update:globalInstruction', preset.global_instruction || '')
  }
}

function onModelTypeChange(nextType) {
  const next = nextType || props.config.model_type
  props.config.model_type = next
  props.config.reference_audio = null
  props.config.voice_description = ''
  selectedClonedVoiceId.value = null
  selectedPreset.value = null

  if (next === 'builtin') {
    // 由父级/最近音色决定，不在这里写死「冰糖」
    props.config.voice_id = props.config.voice_id || null
    emit('restore-builtin-voice')
  } else {
    props.config.voice_id = null
  }
}

function onBuiltinVoiceChange(voiceId) {
  selectedPreset.value = null
  if (voiceId) emit('remember-voice', voiceId)
}

function clearReferenceAudio() {
  props.config.reference_audio = null
  selectedClonedVoiceId.value = null
  if (uploadRef.value) uploadRef.value.clearFiles()
}

async function playClonedRef() {
  if (!selectedClonedVoiceId.value) return
  clonePreviewLoading.value = true
  try {
    clonePreviewUrl.value = `/api/voices/cloned/${selectedClonedVoiceId.value}/audio`
    await nextTick()
    cloneAudio.value?.play()
  } finally {
    clonePreviewLoading.value = false
  }
}

async function removeClonedVoice() {
  if (!selectedClonedVoiceId.value) return
  try {
    await ElMessageBox.confirm('删除该克隆音色及其参考音频？', '删除克隆音色', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  const id = selectedClonedVoiceId.value
  try {
    await api.delete(`/api/voices/cloned/${id}`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
    return
  }
  clonedVoices.value = clonedVoices.value.filter((v) => v.id !== id)
  props.config.reference_audio = null
  selectedClonedVoiceId.value = null
  clonePreviewUrl.value = ''
  ElMessage.success('已删除')
}

function onSelectClonedVoice(id) {
  clonePreviewUrl.value = ''
  if (!id) {
    props.config.reference_audio = null
    return
  }
  const voice = clonedVoices.value.find((v) => v.id === id)
  if (voice) props.config.reference_audio = voice.reference_path
}

function onUploadExceed() {
  ElMessage.warning('请先移除已上传的参考音频，再上传新的')
}

async function onAudioUpload(file) {
  // el-upload 会先触发 ready，避免空跑
  if (!file || !file.raw) return
  if (file.status && file.status !== 'ready') return

  const suffix = (file.name || file.raw.name || '').toLowerCase().split('.').pop()
  if (!['wav', 'mp3'].includes(suffix)) {
    ElMessage.error('仅支持 wav / mp3')
    return
  }
  if (file.raw.size > 10 * 1024 * 1024) {
    ElMessage.error('文件不能超过 10MB')
    return
  }
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('audio', file.raw)
    const name = `clone_${Date.now()}`
    const res = await api.post(`/api/voices/clone?name=${encodeURIComponent(name)}`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    props.config.reference_audio = res.data.reference_path
    selectedClonedVoiceId.value = res.data.id
    clonedVoices.value = [res.data, ...clonedVoices.value]
    ElMessage.success('参考音频已上传')
  } catch (error) {
    ElMessage.error('上传失败: ' + (error.response?.data?.detail || error.message))
    props.config.reference_audio = null
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.voice-panel {
  background: var(--studio-panel);
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius);
  padding: 18px;
}

.panel-header {
  margin-bottom: 14px;
}

.ch-badge {
  display: inline-flex;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--studio-blue);
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--studio-blue-dim);
  border: 1px solid rgba(106, 168, 232, 0.35);
}

.opt-desc {
  color: var(--studio-muted);
  font-size: 12px;
  margin-left: 8px;
}

.tip {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--studio-muted);
  line-height: 1.4;
}

.row-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

@media (max-width: 420px) {
  .row-2 {
    grid-template-columns: 1fr;
  }
}

.upload-area :deep(.el-upload-dragger) {
  padding: 18px;
}

.upload-preview,
.upload-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  color: var(--studio-muted);
}

.upload-preview.ok {
  color: var(--studio-green);
}

.try-btn {
  width: 100%;
  margin-top: 4px;
}

.try-audio {
  width: 100%;
  height: 32px;
  margin-top: 8px;
}

.clone-row {
  display: flex;
  gap: 8px;
  width: 100%;
  align-items: center;
}
</style>
