import asyncio
import base64
import json

import pytest
from aioresponses import aioresponses
from pytest_mock import MockerFixture

from tests.fakedata.conversation import (
    create_fake_agent,
    create_fake_streaming_conversation_factory,
    create_fake_vonage_phone_conversation_with_streaming_conversation_pipeline,
)
from tests.fakedata.id import generate_uuid
from vocode.streaming.action.dtmf import (
    DTMFParameters,
    DTMFVocodeActionConfig,
    TwilioDTMF,
    VonageDTMF,
)
from vocode.streaming.models.actions import ActionInput
from vocode.streaming.models.agent import ChatGPTAgentConfig
from vocode.streaming.models.audio import AudioEncoding
from vocode.streaming.models.telephony import VonageConfig
from vocode.streaming.output_device.twilio_output_device import TwilioOutputDevice
from vocode.streaming.utils import create_conversation_id
from vocode.streaming.utils.dtmf_utils import DTMFToneGenerator


@pytest.mark.asyncio
async def test_vonage_dtmf_press_digits(mocker, mock_env):
    action = VonageDTMF(action_config=DTMFVocodeActionConfig())
    vonage_uuid = generate_uuid()
    digits = "1234"

    vonage_config = VonageConfig(
        api_key="api_key",
        api_secret="api_secret",
        application_id="application_id",
        private_key="-----BEGIN PRIVATE KEY-----\nasdf\n-----END PRIVATE KEY-----",
    )
    vonage_phone_conversation_mock = (
        create_fake_vonage_phone_conversation_with_streaming_conversation_pipeline(
            mocker,
            streaming_conversation_factory=create_fake_streaming_conversation_factory(
                mocker,
                agent=create_fake_agent(
                    mocker,
                    agent_config=ChatGPTAgentConfig(
                        prompt_preamble="",
                        actions=[action.action_config],
                    ),
                ),
            ),
            vonage_config=vonage_config,
            vonage_uuid=vonage_uuid,
        )
    )
    mocker.patch("vonage.Client._create_jwt_auth_string", return_value=b"asdf")

    vonage_phone_conversation_mock.pipeline.actions_worker.attach_state(action)

    assert (
        vonage_phone_conversation_mock.create_vonage_client().get_telephony_config()
        == vonage_config
    )

    with aioresponses() as m:
        m.put(
            f"https://api.nexmo.com/v1/calls/{vonage_uuid}/dtmf",
            status=200,
        )
        action_output = await action.run(
            action_input=ActionInput(
                action_config=DTMFVocodeActionConfig(),
                conversation_id=create_conversation_id(),
                params=DTMFParameters(buttons=digits),
            )
        )

        assert action_output.response.success is True


@pytest.fixture
def mock_twilio_output_device(mocker: MockerFixture):
    output_device = TwilioOutputDevice()
    output_device.ws = mocker.AsyncMock()
    output_device.stream_sid = "stream_sid"
    return output_device


@pytest.fixture
def mock_twilio_phone_conversation(
    mocker: MockerFixture, mock_twilio_output_device: TwilioOutputDevice
):
    twilio_phone_conversation_mock = mocker.MagicMock()
    twilio_phone_conversation_mock.pipeline.output_device = mock_twilio_output_device
    return twilio_phone_conversation_mock


@pytest.mark.asyncio
async def test_twilio_dtmf_press_digits(
    mocker, mock_env, mock_twilio_phone_conversation, mock_twilio_output_device: TwilioOutputDevice
):
    action = TwilioDTMF(action_config=DTMFVocodeActionConfig())
    action.twilio_phone_conversation = mock_twilio_phone_conversation
    digits = "1234"
    twilio_sid = "twilio_sid"

    action_output = await action.run(
        action_input=ActionInput(
            action_config=DTMFVocodeActionConfig(),
            conversation_id=create_conversation_id(),
            params=DTMFParameters(buttons=digits),
            twilio_sid=twilio_sid,
        )
    )

    mock_twilio_output_device.start()
    max_wait_seconds = 1
    waited_seconds = 0
    while mock_twilio_output_device.ws.send_text.call_count < len(digits):
        await asyncio.sleep(0.1)
        waited_seconds += 0.1
        if waited_seconds > max_wait_seconds:
            assert False, "Timed out waiting for DTMF tones to be sent"

    assert action_output.response.success
    await mock_twilio_output_device.terminate()

    for digit, call in zip(digits, mock_twilio_output_device.ws.send_text.call_args_list):
        expected_dtmf = DTMFToneGenerator().generate(
            digit, sampling_rate=8000, audio_encoding=AudioEncoding.MULAW
        )
        media_message = json.loads(call[0][0])
        assert media_message["streamSid"] == mock_twilio_output_device.stream_sid
        assert media_message["media"] == {
            "payload": base64.b64encode(expected_dtmf).decode("utf-8")
        }


@pytest.mark.asyncio
async def test_twilio_dtmf_failure(
    mocker, mock_env, mock_twilio_phone_conversation, mock_twilio_output_device: TwilioOutputDevice
):
    action = TwilioDTMF(action_config=DTMFVocodeActionConfig())
    action.twilio_phone_conversation = mock_twilio_phone_conversation
    digits = "****"
    twilio_sid = "twilio_sid"

    action_output = await action.run(
        action_input=ActionInput(
            action_config=DTMFVocodeActionConfig(),
            conversation_id=create_conversation_id(),
            params=DTMFParameters(buttons=digits),
        )
    )

    assert not action_output.response.success
    assert action_output.response.message == "Invalid DTMF buttons, can only accept 0-9"
