"""scrapli.driver.network_driver"""
import logging
import re
import warnings
from abc import ABC, abstractmethod
from collections import namedtuple
from typing import Any, Dict, List, Optional, Union

from scrapli.driver.generic_driver import GenericDriver
from scrapli.exceptions import CouldNotAcquirePrivLevel, UnknownPrivLevel
from scrapli.helper import get_prompt_pattern
from scrapli.response import Response

PrivilegeLevel = namedtuple(
    "PrivilegeLevel",
    "pattern "
    "name "
    "deescalate_priv "
    "deescalate "
    "escalate_priv "
    "escalate "
    "escalate_auth "
    "escalate_prompt "
    "requestable "
    "level",
)

NoPrivLevel = PrivilegeLevel("", "", "", "", "", "", "", "", "", "")


PRIVS: Dict[str, PrivilegeLevel] = {}

LOG = logging.getLogger("scrapli_base")


class NetworkDriver(GenericDriver, ABC):
    @abstractmethod
    def __init__(
        self, auth_secondary: str = "", **kwargs: Any,
    ):
        """
        BaseNetworkDriver Object

        Args:
            auth_secondary: password to use for secondary authentication (enable)
            **kwargs: keyword args to pass to inherited class(es)

        Returns:
            N/A  # noqa: DAR202

        Raises:
            N/A

        """
        super().__init__(**kwargs)

        self.auth_secondary = auth_secondary
        self.privs = PRIVS
        self.default_desired_priv: str = ""
        self._current_priv_level = NoPrivLevel

        self.textfsm_platform: str = ""
        self.genie_platform: str = ""
        self.failed_when_contains: List[str] = []

    def _determine_current_priv(self, current_prompt: str) -> PrivilegeLevel:
        """
        Determine current privilege level from prompt string

        Args:
            current_prompt: string of current prompt

        Returns:
            PrivilegeLevel: NamedTuple of current privilege level

        Raises:
            UnknownPrivLevel: if privilege level cannot be determined

        """
        for priv_level in self.privs.values():
            prompt_pattern = get_prompt_pattern("", priv_level.pattern)
            if re.search(prompt_pattern, current_prompt.encode()):
                return priv_level
        raise UnknownPrivLevel

    def _escalate(self) -> None:
        """
        Escalate to the next privilege level up

        Args:
            N/A

        Returns:
            N/A  # noqa: DAR202

        Raises:
            UnknownPrivLevel: if priv level cant be attained
            TypeError: if invalid next prompt value

        """
        current_priv = self._determine_current_priv(self.channel.get_prompt())
        if not current_priv.escalate:
            return

        next_priv = self.privs.get(current_priv.escalate_priv, None)
        if next_priv is None:
            raise UnknownPrivLevel(
                f"Could not get next priv level, current priv is {current_priv.name}"
            )
        next_prompt = next_priv.pattern
        if current_priv.escalate_auth:
            if not self.auth_secondary:
                err = (
                    "Privilege escalation generally requires an `auth_secondary` password, "
                    "but none is set!"
                )
                msg = f"***** {err} {'*' * (80 - len(err))}"
                fix = (
                    "scrapli will try to escalate privilege without entering a password but may "
                    "fail.\nSet an `auth_secondary` password if your device requires a password to "
                    "increase privilege, otherwise ignore this message."
                )
                warning = "\n" + msg + "\n" + fix + "\n" + msg
                warnings.warn(warning)
            else:
                escalate_cmd: str = current_priv.escalate
                escalate_prompt: str = current_priv.escalate_prompt
                escalate_auth = self.auth_secondary
                if not isinstance(next_prompt, str):
                    raise TypeError(
                        f"got {type(next_prompt)} for {current_priv.name} escalate priv, "
                        "expected str"
                    )
                self.channel.send_inputs_interact(
                    [escalate_cmd, escalate_prompt, escalate_auth, next_prompt],
                    hidden_response=True,
                )
                self.channel.comms_prompt_pattern = next_priv.pattern
                return
        self.channel.comms_prompt_pattern = next_priv.pattern
        self.channel.send_input(current_priv.escalate)

    def _deescalate(self) -> None:
        """
        Deescalate to the next privilege level down

        Args:
            N/A

        Returns:
            N/A  # noqa: DAR202

        Raises:
            UnknownPrivLevel: if no default priv level set to deescalate to

        """
        current_priv = self._determine_current_priv(self.channel.get_prompt())
        if current_priv.deescalate:
            next_priv = self.privs.get(current_priv.deescalate_priv, None)
            if not next_priv:
                raise UnknownPrivLevel(
                    "NetworkDriver has no default priv levels, set them or use a network driver"
                )
            self.channel.comms_prompt_pattern = next_priv.pattern
            self.channel.send_input(current_priv.deescalate)

    def acquire_priv(self, desired_priv: str) -> None:
        """
        Acquire desired priv level

        Args:
            desired_priv: string name of desired privilege level
                (see scrapli.driver.<driver_category.device_type>.driver for levels)

        Returns:
            N/A  # noqa: DAR202

        Raises:
            CouldNotAcquirePrivLevel: if requested priv level not attained

        """
        priv_attempt_counter = 0
        while True:
            current_priv = self._determine_current_priv(self.channel.get_prompt())
            if current_priv == self.privs[desired_priv]:
                self._current_priv_level = current_priv
                return
            if priv_attempt_counter > len(self.privs):
                raise CouldNotAcquirePrivLevel(
                    f"Could not get to '{desired_priv}' privilege level."
                )
            if current_priv.level > self.privs[desired_priv].level:
                self._deescalate()
            else:
                self._escalate()
            priv_attempt_counter += 1

    def _update_response(self, response: Response) -> None:
        """
        Update response with network driver specific data

        This happens here as the underlying channel provides a response object but is unaware of any
        of the network/platform specific attributes that may need to get updated

        Args:
            response: response to update

        Returns:
            N/A  # noqa: DAR202

        Raises:
            N/A

        """
        response.textfsm_platform = self.textfsm_platform
        response.genie_platform = self.genie_platform

    def send_command(
        self,
        command: str,
        strip_prompt: bool = True,
        failed_when_contains: Optional[Union[str, List[str]]] = None,
    ) -> Response:
        """
        Send a command

        Super method will raise TypeError if anything but a string is passed here!

        Args:
            command: string to send to device in privilege exec mode
            strip_prompt: True/False strip prompt from returned output
            failed_when_contains: string or list of strings indicating failure if found in response

        Returns:
            Response: Scrapli Response object

        Raises:
            N/A

        """
        if self._current_priv_level.name != self.default_desired_priv:
            self.acquire_priv(self.default_desired_priv)

        if failed_when_contains is None:
            failed_when_contains = self.failed_when_contains

        response = super().send_command(
            command, strip_prompt=strip_prompt, failed_when_contains=failed_when_contains
        )

        self._update_response(response)

        return response

    def send_commands(
        self,
        commands: List[str],
        strip_prompt: bool = True,
        failed_when_contains: Optional[Union[str, List[str]]] = None,
    ) -> List[Response]:
        """
        Send multiple commands

        Super method will raise TypeError if anything but a list of strings is passed here!

        Args:
            commands: list of strings to send to device in privilege exec mode
            strip_prompt: True/False strip prompt from returned output
            failed_when_contains: string or list of strings indicating failure if found in response

        Returns:
            responses: list of Scrapli Response objects

        Raises:
            N/A

        """
        if self._current_priv_level.name != self.default_desired_priv:
            self.acquire_priv(self.default_desired_priv)

        if failed_when_contains is None:
            failed_when_contains = self.failed_when_contains

        responses = super().send_commands(
            commands, strip_prompt=strip_prompt, failed_when_contains=failed_when_contains
        )

        for response in responses:
            self._update_response(response)

        return responses

    def send_interactive(self, interact: List[str], hidden_response: bool = False) -> Response:
        """
        Send inputs in an interactive fashion; used to handle prompts

        accepts inputs and looks for expected prompt;
        sends the appropriate response, then waits for the "finale"
        returns the results of the interaction

        could be "chained" together to respond to more than a "single" staged prompt

        Args:
            interact: list of four string elements representing...
                channel_input - initial input to send
                expected_prompt - prompt to expect after initial input
                response - response to prompt
                final_prompt - final prompt to expect
            hidden_response: True/False response is hidden (i.e. password input)

        Returns:
            Response: scrapli Response object

        Raises:
            N/A

        """
        if self._current_priv_level.name != self.default_desired_priv:
            self.acquire_priv(self.default_desired_priv)
        response = self.channel.send_inputs_interact(interact, hidden_response)
        return response

    def send_configs(
        self,
        configs: Union[str, List[str]],
        strip_prompt: bool = True,
        failed_when_contains: Optional[Union[str, List[str]]] = None,
    ) -> List[Response]:
        """
        Send configuration(s)

        Args:
            configs: string or list of strings to send to device in config mode
            strip_prompt: True/False strip prompt from returned output
            failed_when_contains: string or list of strings indicating failure if found in response

        Returns:
            responses: List of Scrape Response objects

        Raises:
            N/A

        """
        if isinstance(configs, str):
            configs = [configs]

        self.acquire_priv("configuration")

        if failed_when_contains is None:
            failed_when_contains = self.failed_when_contains

        responses = []
        for config in configs:
            responses.append(
                super().send_command(
                    config, strip_prompt=strip_prompt, failed_when_contains=failed_when_contains
                )
            )

        for response in responses:
            self._update_response(response)

        self.acquire_priv(self.default_desired_priv)
        return responses
