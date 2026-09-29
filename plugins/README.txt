========================================
  plugins/ —— 自定义格式扩展目录
========================================

把 .py 插件文件放进本目录即可被自动加载，无需修改程序本体。

【最小插件模板】

    from unpacker.extractors.base import BaseExtractor
    from unpacker.registry import register
    from unpacker.models import ArchiveKind, ExtractStatus


    @register
    class MyPakExtractor(BaseExtractor):
        name = "我的 PAK"                  # 解包器名称（唯一）
        kinds = (ArchiveKind.GAME_PACK,)   # 处理的容器类型
        priority = 40                      # 数字越小越优先（通用雕刻器为 90）

        def can_handle(self, info):
            return info.path.suffix.lower() == ".mypak"

        def extract(self, info, ctx):
            out = self._prepare_dir(ctx)
            written = []
            # ... 在此解析你的格式 ...
            # written.append(self._write_bytes(ctx, out, 文件名, 数据))
            return self._result(info, ExtractStatus.SUCCESS,
                                files=written, message=f"解出 {len(written)} 个文件")

【可用能力】

  ctx.output_dir          输出目录
  ctx.passwords           密码候选列表
  ctx.conflict            重名策略（ConflictPolicy 枚举）
  ctx.encoding            成员名编码（"auto" 或 "gbk" / "utf-8" …）
  ctx.progress(done, total, name)   上报进度（驱动界面进度条）
  ctx.info / warn / error(msg)      写日志
  ctx.check_cancel()      检查用户是否取消（长循环里请定期调用）
  ctx.bytes_written       已写出字节数（本插件落盘助手会自动累加）

  self._prepare_dir(ctx)            创建并返回输出目录（绝对路径）
  self._result(info, 状态, files=..., message=..., password=...)  构造结果

【落盘助手 —— 按数据来源选，大文件不要用 _write_bytes】

  self._write_bytes(ctx, out, 名, 数据)                内存中的 bytes -> 文件
  self._copy_stream(ctx, out, 名, 文件对象)            流 -> 文件（分块，内存 O(1MiB)）
  self._write_slice(ctx, out, 名, src, offset, size)   已打开文件的区间 -> 文件（mmap 友好）

  三者都会：做路径穿越防护、按 ctx.conflict 处理重名、中途失败时删除半成品。
  **返回值为相对输出目录的字符串；按「跳过」策略未写盘时返回 None**，
  因此请这样收集：

      rel = self._copy_stream(ctx, out, name, src)
      if rel is not None:
          written.append(rel)

  只需要解析出最终落盘路径而不立即写时，用 self._target(ctx, 名, out)。

【密码表批量尝试】

      ok, used, value, err = self._try_passwords(
          ctx, attempt, candidates=[None, *ctx.passwords])

  attempt(pwd) 在函数内部**已经执行完毕**，value 就是它的返回值；
  请不要在成功后再次调用 attempt，否则会重复落盘
  （在「保留两者」策略下会生成 xxx_1 重复文件）。

【相对路径成员名的解码】

      from unpacker.utils import decode_member_name
      name = decode_member_name(raw_name_bytes, ctx.encoding, fallback="cp437")

  会按 auto / gbk / big5 / shift_jis 依次尝试，只在解出 CJK 时才采纳回退结果。

【注意】
- 文件名一律经过安全清洗（非法字符、Windows 保留设备名、超长截断）。
- 插件抛异常不会影响主程序，只会跳过该插件。
- 用户级插件目录同样生效：%USERPROFILE%\.winunpack\plugins\
