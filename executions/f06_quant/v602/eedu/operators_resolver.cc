// Auto-generated
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
void RegisterModelOperators(tflite::MicroMutableOpResolver<9>& resolver) {
  resolver.AddCast();
  resolver.AddConv2d();
  resolver.AddDequantize();
  resolver.AddExpandDims();
  resolver.AddFullyConnected();
  resolver.AddGather();
  resolver.AddLogistic();
  resolver.AddReduceMax();
  resolver.AddReshape();
}