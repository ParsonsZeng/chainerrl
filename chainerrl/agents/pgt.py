from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from future import standard_library
from builtins import *  # NOQA
standard_library.install_aliases()

import chainer
from chainer import cuda
import chainer.functions as F
import numpy as np

from chainerrl.agents import dqn


class PGT(dqn.DQN):
    """Policy Gradient Theorem.

    This algorithm optimizes a Q-function and a stochastic policy based on
    policy gradients computed by the policy gradient theorem. Unlike DDPG and
    SVG(0), it does not use value grdients.
    """

    def __init__(self, model, actor_optimizer, critic_optimizer, replay_buffer,
                 gamma, explorer, beta=1e-2, **kwargs):
        super().__init__(model, None, replay_buffer, gamma, explorer, **kwargs)

        # Aliases for convenience
        self.q_function = self.model['q_function']
        self.policy = self.model['policy']
        self.target_q_function = self.target_model['q_function']
        self.target_policy = self.target_model['policy']

        self.actor_optimizer = actor_optimizer
        self.critic_optimizer = critic_optimizer
        self.beta = beta

        self.average_actor_loss = 0.0
        self.average_critic_loss = 0.0

    def update(self, experiences, errors_out=None):
        """Update the model from experiences."""

        batch_size = len(experiences)

        # Store necessary data in arrays
        batch_state = self._batch_states(
            [elem['state'] for elem in experiences])

        batch_actions = self.xp.asarray(
            [elem['action'] for elem in experiences])

        batch_next_state = self._batch_states(
            [elem['next_state'] for elem in experiences])

        batch_rewards = self.xp.asarray(
            [[elem['reward']] for elem in experiences], dtype=np.float32)

        batch_terminal = self.xp.asarray(
            [[elem['is_state_terminal']] for elem in experiences],
            dtype=np.float32)

        # Update Q-function
        def compute_critic_loss():

            with chainer.no_backprop_mode():
                pout = self.target_policy(batch_next_state, test=True)
                next_actions = pout.sample()
                next_q = self.target_q_function(batch_next_state, next_actions,
                                                test=True)

                target_q = batch_rewards + self.gamma * \
                    (1.0 - batch_terminal) * next_q

            predict_q = self.q_function(batch_state, batch_actions, test=False)

            loss = F.mean_squared_error(target_q, predict_q)

            # Update stats
            self.average_critic_loss *= self.average_loss_decay
            self.average_critic_loss += ((1 - self.average_loss_decay) *
                                         float(loss.data))

            return loss

        def compute_actor_loss():
            pout = self.policy(batch_state, test=False)
            sampled_actions = pout.sample()
            sampled_actions.creator = None
            q = self.q_function(batch_state, sampled_actions, test=True)
            log_probs = pout.log_prob(sampled_actions)
            v = self.q_function(
                batch_state, pout.most_probable, test=True)
            advantage = F.reshape(q - v, (batch_size,))
            advantage = chainer.Variable(advantage.data)
            loss = - F.sum(advantage * log_probs + self.beta * pout.entropy) \
                / batch_size

            # Update stats
            self.average_actor_loss *= self.average_loss_decay
            self.average_actor_loss += ((1 - self.average_loss_decay) *
                                        float(loss.data))

            return loss

        self.critic_optimizer.update(compute_critic_loss)
        self.actor_optimizer.update(compute_actor_loss)

    def act(self, state):

        s = self._batch_states([state])
        action = self.policy(s, test=True).sample()
        # Q is not needed here, but log it just for information
        q = self.q_function(s, action, test=True)

        # Update stats
        self.average_q *= self.average_q_decay
        self.average_q += (1 - self.average_q_decay) * float(q.data)

        self.logger.debug('t:%s a:%s q:%s',
                          self.t, action.data[0], q.data)
        return cuda.to_cpu(action.data[0])

    def select_action(self, state):
        return self.explorer.select_action(
            self.t, lambda: self.act(state))

    def get_stats_keys(self):
        return ('average_q', 'average_actor_loss', 'average_critic_loss')

    def get_stats_values(self):
        return (self.average_q,
                self.average_actor_loss,
                self.average_critic_loss)

    @property
    def saved_attributes(self):
        return ('model', 'target_model', 'actor_optimizer', 'critic_optimizer')
