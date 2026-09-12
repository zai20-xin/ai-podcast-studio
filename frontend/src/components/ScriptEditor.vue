<template>
  <div class="script-editor">
    <div class="editor-toolbar">
      <div class="meta">
        <span class="studio-mono">{{ lineCount }} lines</span>
      </div>
      <div class="toolbar-right">
        <el-button size="small" :loading="rewriting === 'colloquial'" @click="rewrite('colloquial')">
          口语化
        </el-button>
        <el-button size="small" :loading="rewriting === 'shorten'" @click="rewrite('shorten')">
          压短
        </el-button>
        <el-button size="small" :loading="rewriting === 'polish'" @click="rewrite('polish')">
          润色
        </el-button>
        <el-divider direction="vertical" />
        <el-button size="small" @click="insertTag('轻笑')">(轻笑)</el-button>
        <el-button size="small" @click="insertTag('停顿')">(停顿)</el-button>
        <el-button size="small" @click="insertTag('叹气')">(叹气)</el-button>
        <el-button size="small" @click="insertTag('深呼吸')">(深呼吸)</el-button>
      </div>
    </div>
    <el-input
      ref="textareaRef"
      :model-value="modelValue"
      type="textarea"
      :rows="14"
      class="script-area"
      placeholder="输入脚本，例如：&#10;&#10;A: 你好，欢迎收听&#10;B: 今天我们聊聊人工智能&#10;&#10;支持中文角色名（小凡：…）、英文角色名（A: …）、全角/半角冒号。&#10;【开场】等章节标记会自动跳过。&#10;可插入 (轻笑)(停顿)(叹气)(深呼吸) 控制语气。"
      @input="$emit('update:modelValue', $event)"
    />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const props = defineProps({
  modelValue: String,
  mode: { type: String, default: 'dialogue' },
})

const emit = defineEmits(['update:modelValue'])

const textareaRef = ref(null)
const rewriting = ref(null)

const lineCount = computed(() =>
  props.modelValue ? props.modelValue.split('\n').filter((l) => l.trim()).length : 0
)

function insertTag(tag) {
  const textarea = textareaRef.value?.textarea
  if (!textarea) return
  const start = textarea.selectionStart
  const end = textarea.selectionEnd
  const text = props.modelValue || ''
  const newText = text.slice(0, start) + `(${tag})` + text.slice(end)
  emit('update:modelValue', newText)
}

async function rewrite(action) {
  const script = (props.modelValue || '').trim()
  if (script.length < 10) {
    ElMessage.warning('请先写一点脚本内容')
    return
  }
  let ratio = null
  if (action === 'shorten') {
    try {
      const { value } = await ElMessageBox.prompt(
        '保留大约多少内容？（30–95）',
        '压短脚本',
        {
          confirmButtonText: '压短',
          cancelButtonText: '取消',
          inputValue: '60',
          inputPattern: /^\d{1,2}$/,
          inputErrorMessage: '请输入 30–95 的数字',
        }
      )
      const n = Number(value)
      if (n < 30 || n > 95) {
        ElMessage.warning('请输入 30–95')
        return
      }
      ratio = n / 100
    } catch {
      return
    }
  }

  rewriting.value = action
  try {
    const res = await api.post('/api/script/rewrite', {
      script,
      mode: props.mode,
      action,
      ratio,
    })
    emit('update:modelValue', res.data.script)
    ElMessage.success(
      action === 'shorten' ? '已压短，可继续手改' : action === 'polish' ? '已润色' : '已口语化'
    )
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '改写失败，请检查 LLM 设置')
  } finally {
    rewriting.value = null
  }
}
</script>

<style scoped>
.editor-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.meta {
  color: var(--studio-muted);
  font-size: 12px;
}

.toolbar-right {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.script-area :deep(.el-textarea__inner) {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.65;
  min-height: 280px !important;
  resize: vertical;
}
</style>
