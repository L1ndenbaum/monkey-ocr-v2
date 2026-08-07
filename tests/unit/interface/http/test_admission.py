import pytest

from monkeyocr.interface.http.admission import RequestAdmission


@pytest.mark.asyncio
async def test_admission_rejects_without_waiting_at_limit() -> None:
    admission = RequestAdmission(1)

    assert await admission.try_acquire() is True
    assert await admission.try_acquire() is False
    await admission.release()
    assert await admission.try_acquire() is True
