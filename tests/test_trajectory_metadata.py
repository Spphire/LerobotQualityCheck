import unittest

import server


class TrajectoryMetadataTest(unittest.TestCase):
    def test_rollout_uses_teleop_transform(self):
        metadata = server.trajectory_metadata_for_episode(
            {"info": {}},
            {"device_type": "rollout", "collection_mode": "inference_rollout"},
        )

        self.assertEqual(metadata["transform"], "teleop_rx_minus_90")
        self.assertEqual(metadata["world_up_axis"], "y")

    def test_unknown_device_keeps_identity_transform(self):
        metadata = server.trajectory_metadata_for_episode(
            {"info": {}},
            {"device_type": "unknown", "collection_mode": "inference_rollout"},
        )

        self.assertEqual(metadata["transform"], "identity")


if __name__ == "__main__":
    unittest.main()
