#include "tflite_runner.h"
#include "model_resolver.h"
#include "models_mgr.h"

#include <stdio.h>
#include <stdlib.h>
#include <new>
#include <cstring>

#include <tensorflow/lite/micro/micro_mutable_op_resolver.h>
#include <tensorflow/lite/micro/micro_interpreter.h>
#include <tensorflow/lite/schema/schema_generated.h>
//#include <tensorflow/lite/version.h>
#include "esp_timer.h"

static uint8_t *s_arena = nullptr;
static size_t s_arena_size = 0;

static tflite::MicroMutableOpResolver<MODEL_OPERATOR_COUNT> *s_resolver = nullptr;

// Un único interpreter activo, construido por placement new sobre almacenamiento estático.
// Evita new/delete en heap en cada inferencia.
alignas(tflite::MicroInterpreter)
static unsigned char s_interpreter_storage[sizeof(tflite::MicroInterpreter)];
static tflite::MicroInterpreter *s_interpreter = nullptr;

static void destroy_interpreter() {
  if (s_interpreter) {
    s_interpreter->~MicroInterpreter();
    s_interpreter = nullptr;
  }
}

static size_t compute_max_arena_required(const model_t *models, size_t model_count) {
  size_t max_arena = 0;
  for (size_t i = 0; i < model_count; ++i) {
    if (models[i].arena_required > max_arena) {
      max_arena = models[i].arena_required;
    }
  }
  return max_arena;
}

size_t count_models_that_fit() {
  tflite::MicroMutableOpResolver<MODEL_OPERATOR_COUNT> resolver;
  SetupModelResolver(resolver);

  size_t fit = 0;

  for (size_t i = 0; i < g_models_count; ++i) {
    const tflite::Model *flat = tflite::GetModel(g_models[i].data);
    if (!flat) {
      break;
    }

    uint8_t *arena = (uint8_t *)malloc(g_models[i].arena_required);
    if (!arena) {
      break;
    }

    tflite::MicroInterpreter test_interp(
        flat,
        resolver,
        arena,
        g_models[i].arena_required,
        nullptr);

    if (test_interp.AllocateTensors() == kTfLiteOk) {
      ++fit;
      free(arena);
    } else {
      free(arena);
      break;
    }
  }

  return fit;
}

void tflite_runner_init(const model_t *models, size_t model_count) {
  if (s_arena) {
    return;
  }

  s_arena_size = compute_max_arena_required(models, model_count);
  if (s_arena_size == 0) {
    printf("[TFLM] ERROR: max arena size is 0\n");
    abort();
  }

  s_arena = (uint8_t *)std::malloc(s_arena_size);
  if (!s_arena) {
    printf("[TFLM] ERROR: cannot allocate shared arena of %zu bytes\n", s_arena_size);
    abort();
  }

  s_resolver = new tflite::MicroMutableOpResolver<MODEL_OPERATOR_COUNT>();
  SetupModelResolver(*s_resolver);
}

int tflite_runner_run(const model_t *model,
                      const event_t *input_data,
                      size_t input_len,
                      int *result,
                      size_t output_len) {
  (void)output_len;

  if (!model || !input_data || !result) return -1;
  if (!s_arena || !s_resolver) return -1;

  // MUY IMPORTANTE:
  // No es seguro mantener varios interpreters sobre la misma arena.
  // Se destruye el anterior y se reconstruye uno nuevo sobre almacenamiento estático.
  destroy_interpreter();

  const tflite::Model *flat = tflite::GetModel(model->data);
  if (!flat) return -1;

  // Limpiamos la arena compartida antes de reconfigurar el nuevo modelo.
  // Esto evita residuos de asignaciones previas en una arena reutilizada.
  std::memset(s_arena, 0, s_arena_size);

  s_interpreter = new (s_interpreter_storage) tflite::MicroInterpreter(
      flat,
      *s_resolver,
      s_arena,
      s_arena_size,
      nullptr);

  if (s_interpreter->AllocateTensors() != kTfLiteOk) {
    destroy_interpreter();
    return -1;
  }

  TfLiteTensor *in = s_interpreter->input(0);
  if (!in) {
    destroy_interpreter();
    return -1;
  }

  size_t copy_count = input_len;
  if (copy_count > (size_t)in->bytes) {
    copy_count = (size_t)in->bytes;
  }

  // Limpiamos solo el tensor de entrada para evitar residuos si el nuevo input
  // es más corto que el tensor completo.
  std::memset(in->data.int8, 0, in->bytes);
  for (size_t i = 0; i < copy_count; ++i) {
    in->data.int8[i] = (int8_t)input_data[i];
  }

  if (s_interpreter->Invoke() != kTfLiteOk) {
    destroy_interpreter();
    return -1;
  }

  TfLiteTensor *out = s_interpreter->output(0);
  if (!out) {
    destroy_interpreter();
    return -1;
  }

  const int8_t quantized_value = out->data.int8[0];
  const float scale = out->params.scale;
  const int zero_point = out->params.zero_point;
  const float prob = (quantized_value - zero_point) * scale;

  *result = (prob > model->threshold) ? 1 : 0;
  return 0;
}