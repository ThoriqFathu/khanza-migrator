from PySide6.QtCore import QObject, Signal, Slot


class Worker(QObject):
    progress = Signal(str, int, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, function):
        super().__init__()
        self.function = function

    @Slot()
    def run(self):
        print(">>> Worker.run() DIPANGGIL")

        try:
            print(">>> menjalankan function")
            result = self.function(self.progress.emit)

            print(">>> function selesai")
            self.succeeded.emit(result)

        except Exception as exc:
            print(f">>> Worker ERROR: {exc}")
            self.failed.emit(str(exc))

        finally:
            print(">>> Worker.finished")
            self.finished.emit()
