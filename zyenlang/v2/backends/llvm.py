from __future__ import annotations

from ..ir import IRProgram


class LLVMBackend:
    """Reserved backend boundary for native LLVM IR emission.

    The bootstrap keeps this explicit stub so frontend and optimization passes
    cannot accidentally depend on C source details.
    """

    name = "llvm"

    def emit(self, program: IRProgram) -> str:
        del program
        raise NotImplementedError("the LLVM backend starts after the C reference backend is stable")
