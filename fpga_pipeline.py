from common import *
import struct


class FPGADynamicPipeline:
    def __init__(self):
        # 流水线阶段耗时（论文4.10节：总耗时22ms）
        self.stage_latency = {
            "input_decode": 5,    # 输入解码：5ms
            "dynamic_adapt": 4,   # 动态适配：4ms（新增阶段）
            "data_compute": 8,    # 数据计算：8ms（并行度128路）
            "output_encode": 5    # 输出编码：5ms
        }
        # Value尺寸阈值（小Value<1KB，大Value≥1KB，论文4.10节）
        self.small_value_threshold = 1024  # 1KB

    def process_value(self, value: bytes, blob_id: str) -> Tuple[bytes, float]:
        """四阶段处理Value（论文4.10节）"""
        total_latency = 0.0
        value_size = len(value)
        
        # 1. 输入解码：参数化解析（无需重编译固件）
        decoded = self._input_decode(value)
        total_latency += self.stage_latency["input_decode"]
        
        # 2. 动态适配：按Value大小调整处理粒度
        adapted = self._dynamic_adapt(decoded, value_size)
        total_latency += self.stage_latency["dynamic_adapt"]
        
        # 3. 数据计算：筛选（并行度128路）+ CRC校验（速率1GB/s）
        computed, checksum = self._data_compute(adapted)
        total_latency += self.stage_latency["data_compute"]
        
        # 4. 输出编码：生成Blob片段，流式写入SSD
        encoded = self._output_encode(computed, blob_id, checksum)
        total_latency += self.stage_latency["output_encode"]
        
        print(f"[FPGA Pipeline] Processed Value (size={value_size}B), Total Latency={total_latency}ms, Checksum={checksum}")
        return encoded, total_latency

    def _input_decode(self, value: bytes) -> dict:
        """输入解码：参数化解析Value头部信息（模拟）"""
        # 假设Value头部4字节为长度，后续为数据
        if len(value) < 4:
            raise ValueError("Invalid Value: missing length header")
        length = struct.unpack(">I", value[:4])[0]
        data = value[4:]
        return {"length": length, "data": data}

    def _dynamic_adapt(self, decoded: dict, value_size: int) -> bytes:
        """动态适配：小Value打包，大Value分块（论文4.10节）"""
        data = decoded["data"]
        if value_size < self.small_value_threshold:
            # 小Value：按1KB打包（减少I/O次数）
            pad_size = self.small_value_threshold - value_size
            adapted = data + b"\x00" * pad_size
            print(f"[Dynamic Adapt] Small Value: padded to 1KB (original={value_size}B)")
        else:
            # 大Value：按4KB分块（避免碎片化）
            chunk_size = 4096
            chunks = [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]
            adapted = b"".join(chunks)
            print(f"[Dynamic Adapt] Large Value: split into {len(chunks)}×4KB chunks")
        return adapted

    def _data_compute(self, adapted: bytes) -> Tuple[bytes, str]:
        """数据计算：筛选有效数据+CRC32校验（速率1GB/s）"""
        # 模拟筛选：移除填充的0字节（小Value场景）
        valid_data = adapted.rstrip(b"\x00") if len(adapted) == 1024 else adapted
        # CRC32校验（模拟1GB/s速率）
        checksum = hashlib.crc32(valid_data).hex()
        return valid_data, checksum

    def _output_encode(self, computed: bytes, blob_id: str, checksum: str) -> bytes:
        """输出编码：生成Blob片段（包含BlobID、校验和、数据）"""
        # 编码格式：BlobID(16B) + Checksum(8B) + Data Length(4B) + Data
        blob_id_bytes = blob_id.encode("utf-8").ljust(16, b"\x00")[:16]
        checksum_bytes = checksum.encode("utf-8").ljust(8, b"\x00")[:8]
        length_bytes = struct.pack(">I", len(computed))
        encoded = blob_id_bytes + checksum_bytes + length_bytes + computed
        return encoded